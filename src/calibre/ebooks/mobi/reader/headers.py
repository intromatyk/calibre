#!/usr/bin/env python
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (absolute_import, print_function)

__license__   = 'GPL v3'
__copyright__ = '2012, Kovid Goyal <kovid@kovidgoyal.net>'
__docformat__ = 'restructuredtext en'

import struct, re, os

from calibre import replace_entities
from calibre.utils.date import parse_date
from calibre.ebooks.mobi import MobiError
from calibre.ebooks.metadata import MetaInformation
from calibre.ebooks.mobi.langcodes import main_language, sub_language, mobi2iana

NULL_INDEX = 0xffffffff

class EXTHHeader(object): # {{{

    def __init__(self, raw, codec, title):
        self.doctype = raw[:4]
        self.length, self.num_items = struct.unpack('>LL', raw[4:12])
        raw = raw[12:]
        pos = 0
        self.mi = MetaInformation(_('Unknown'), [_('Unknown')])
        self.has_fake_cover = True
        self.start_offset = None
        left = self.num_items
        self.kf8_header = None

        while left > 0:
            left -= 1
            id, size = struct.unpack('>LL', raw[pos:pos + 8])
            content = raw[pos + 8:pos + size]
            pos += size
            if id >= 100 and id < 200:
                self.process_metadata(id, content, codec)
            elif id == 203:
                self.has_fake_cover = bool(struct.unpack('>L', content)[0])
            elif id == 201:
                co, = struct.unpack('>L', content)
                if co < NULL_INDEX:
                    self.cover_offset = co
            elif id == 202:
                self.thumbnail_offset, = struct.unpack('>L', content)
            elif id == 501:
                # cdetype
                pass
            elif id == 502:
                # last update time
                pass
            elif id == 503: # Long title
                # Amazon seems to regard this as the definitive book title
                # rather than the title from the PDB header. In fact when
                # sending MOBI files through Amazon's email service if the
                # title contains non ASCII chars or non filename safe chars
                # they are messed up in the PDB header
                try:
                    title = content.decode(codec)
                except:
                    pass
            #else:
            #    print 'unknown record', id, repr(content)
        if title:
            self.mi.title = replace_entities(title)

    def process_metadata(self, id, content, codec):
        if id == 100:
            if self.mi.authors == [_('Unknown')]:
                self.mi.authors = []
            au = content.decode(codec, 'ignore').strip()
            self.mi.authors.append(au)
            if re.match(r'\S+?\s*,\s+\S+', au.strip()):
                self.mi.author_sort = au.strip()
        elif id == 101:
            self.mi.publisher = content.decode(codec, 'ignore').strip()
        elif id == 103:
            self.mi.comments  = content.decode(codec, 'ignore')
        elif id == 104:
            self.mi.isbn      = content.decode(codec, 'ignore').strip().replace('-', '')
        elif id == 105:
            if not self.mi.tags:
                self.mi.tags = []
            self.mi.tags.extend([x.strip() for x in content.decode(codec,
                'ignore').split(';')])
            self.mi.tags = list(set(self.mi.tags))
        elif id == 106:
            try:
                self.mi.pubdate = parse_date(content, as_utc=False)
            except:
                pass
        elif id == 108:
            pass # Producer
        elif id == 113:
            pass # ASIN or UUID
        elif id == 116:
            self.start_offset, = struct.unpack(b'>L', content)
        elif id == 121:
            self.kf8_header, = struct.unpack(b'>L', content)
        #else:
        #    print 'unhandled metadata record', id, repr(content)
# }}}

class BookHeader(object):

    def __init__(self, raw, ident, user_encoding, log, try_extra_data_fix=False):
        self.log = log
        self.compression_type = raw[:2]
        self.records, self.records_size = struct.unpack('>HH', raw[8:12])
        self.encryption_type, = struct.unpack('>H', raw[12:14])
        if ident == 'TEXTREAD':
            self.codepage = 1252
        if len(raw) <= 16:
            self.codec = 'cp1252'
            self.extra_flags = 0
            self.title = _('Unknown')
            self.language = 'ENGLISH'
            self.sublanguage = 'NEUTRAL'
            self.exth_flag, self.exth = 0, None
            self.ancient = True
            self.first_image_index = -1
            self.mobi_version = 1
        else:
            self.ancient = False
            self.doctype = raw[16:20]
            self.length, self.type, self.codepage, self.unique_id, \
                self.version = struct.unpack('>LLLLL', raw[20:40])

            try:
                self.codec = {
                    1252: 'cp1252',
                    65001: 'utf-8',
                    }[self.codepage]
            except (IndexError, KeyError):
                self.codec = 'cp1252' if not user_encoding else user_encoding
                log.warn('Unknown codepage %d. Assuming %s' % (self.codepage,
                    self.codec))
            # There exists some broken DRM removal tool that removes DRM but
            # leaves the DRM fields in the header yielding a header size of
            # 0xF8. The actual value of max_header_length should be 0xE8 but
            # it's changed to accommodate this silly tool. Hopefully that will
            # not break anything else.
            max_header_length = 0xF8

            if (ident == 'TEXTREAD' or self.length < 0xE4 or
                    self.length > max_header_length or
                    (try_extra_data_fix and self.length == 0xE4)):
                self.extra_flags = 0
            else:
                self.extra_flags, = struct.unpack('>H', raw[0xF2:0xF4])

            if self.compression_type == 'DH':
                self.huff_offset, self.huff_number = struct.unpack('>LL',
                        raw[0x70:0x78])

            toff, tlen = struct.unpack('>II', raw[0x54:0x5c])
            tend = toff + tlen
            self.title = raw[toff:tend] if tend < len(raw) else _('Unknown')
            langcode  = struct.unpack('!L', raw[0x5C:0x60])[0]
            langid    = langcode & 0xFF
            sublangid = (langcode >> 10) & 0xFF
            self.language = main_language.get(langid, 'ENGLISH')
            self.sublanguage = sub_language.get(sublangid, 'NEUTRAL')
            self.mobi_version = struct.unpack('>I', raw[0x68:0x6c])[0]
            self.first_image_index = struct.unpack('>L', raw[0x6c:0x6c + 4])[0]

            self.exth_flag, = struct.unpack('>L', raw[0x80:0x84])
            self.exth = None
            if not isinstance(self.title, unicode):
                self.title = self.title.decode(self.codec, 'replace')
            if self.exth_flag & 0x40:
                try:
                    self.exth = EXTHHeader(raw[16 + self.length:], self.codec,
                            self.title)
                    self.exth.mi.uid = self.unique_id
                    try:
                        self.exth.mi.language = mobi2iana(langid, sublangid)
                    except:
                        self.log.exception('Unknown language code')
                except:
                    self.log.exception('Invalid EXTH header')
                    self.exth_flag = 0

            self.ncxidx = NULL_INDEX
            if len(raw) >= 0xF8:
                self.ncxidx, = struct.unpack_from(b'>L', raw, 0xF4)

            if self.mobi_version >= 8:
                self.skelidx, = struct.unpack_from('>L', raw, 0xFC)

                # Index into <div> sections in raw_ml
                self.dividx, = struct.unpack_from('>L', raw, 0xF8)

                # Index into Other files
                self.othidx, = struct.unpack_from('>L', raw, 0x104)

                # need to use the FDST record to find out how to properly
                # unpack the raw_ml into pieces it is simply a table of start
                # and end locations for each flow piece
                self.fdstidx, = struct.unpack_from('>L', raw, 0xC0)
                self.fdstcnt, = struct.unpack_from('>L', raw, 0xC4)
                # if cnt is 1 or less, fdst section number can be garbage
                if self.fdstcnt <= 1:
                    self.fdstidx = NULL_INDEX
            else: # Null values
                self.skelidx = self.dividx = self.othidx = self.fdstidx = \
                        NULL_INDEX

class MetadataHeader(BookHeader):

    def __init__(self, stream, log):
        self.stream = stream
        self.ident = self.identity()
        self.num_sections = self.section_count()
        if self.num_sections >= 2:
            header = self.header()
            BookHeader.__init__(self, header, self.ident, None, log)
        else:
            self.exth = None

    def identity(self):
        self.stream.seek(60)
        ident = self.stream.read(8).upper()
        if ident not in ['BOOKMOBI', 'TEXTREAD']:
            raise MobiError('Unknown book type: %s' % ident)
        return ident

    def section_count(self):
        self.stream.seek(76)
        return struct.unpack('>H', self.stream.read(2))[0]

    def section_offset(self, number):
        self.stream.seek(78 + number * 8)
        return struct.unpack('>LBBBB', self.stream.read(8))[0]

    def header(self):
        section_headers = []
        # First section with the metadata
        section_headers.append(self.section_offset(0))
        # Second section used to get the length of the first
        section_headers.append(self.section_offset(1))

        end_off = section_headers[1]
        off = section_headers[0]
        self.stream.seek(off)
        return self.stream.read(end_off - off)

    def section_data(self, number):
        start = self.section_offset(number)
        if number == self.num_sections -1:
            end = os.stat(self.stream.name).st_size
        else:
            end = self.section_offset(number + 1)
        self.stream.seek(start)
        try:
            return self.stream.read(end - start)
        except OverflowError:
            self.stream.seek(start)
            return self.stream.read()
