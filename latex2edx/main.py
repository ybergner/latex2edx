#!/usr/bin/python

import xbundle
import os
import sys
import optparse
import urllib
from path import path	# needs path.py
from lxml import etree
from plastexit import plastex2xhtml
from abox import split_args_with_quoted_strings

# from logging import Logger

#-----------------------------------------------------------------------------

DEFAULT_CONFIG = {
    'problem_default_attributes': {
        'showanswer': 'closed',
        'rerandomize': 'never',
     }
}

#-----------------------------------------------------------------------------

class latex2edx(object):
    '''
    latex2edx works in three stages:

    1. use plastex to convert .tex file to .xhtml, with special edX macros (via py & zpts)
    2. clean up .xhtml file and convert into single .xml file, "xbundle" format
    3. convert xbundle into directory with standard XML format files for edX

    This script can be run from any directory.
    '''

    DescriptorTags = ['course','chapter','sequential','vertical','html','problem','video',
                      'conditional', 'combinedopenended', 'randomize' ]

    def __init__(self,
                 fn,
                 fp=None,
                 extra_filters=None,
                 latex_string=None,
                 add_wrap=False,
                 extra_xml_filters=None,
                 verbose=False,
                 output_fn=None,
                 output_dir='',
                 do_merge=False,
                 imurl='images',
                 do_images=True,
                 ):
        '''
        extra_xml_filters = list of functions acting on XML, applied to XHTML
        '''

        if not output_dir:
            output_dir = os.path.abspath('.')
        self.output_dir = path(output_dir)
        imdir = self.output_dir / 'static/images'

        if do_images:	# make directories only if do_images
            if not os.path.exists(self.output_dir):
                os.mkdir(self.output_dir)
            if not os.path.exists(self.output_dir / 'static'):
                os.mkdir(self.output_dir / 'static')
            if not os.path.exists(imdir):
                os.mkdir(imdir)

        self.p2x = plastex2xhtml(fn, fp=fp, extra_filters=extra_filters,
                                 latex_string=latex_string,
                                 add_wrap=add_wrap,
                                 verbose=verbose,
                                 imdir=imdir,
                                 imurl=imurl,
            )
        self.p2x.convert()
        self.xhtml = self.p2x.xhtml
        self.do_merge = do_merge

        if output_fn is None or not output_fn:
            if fn.endswith('.tex'):
                output_fn = fn[:-4]+'.xbundle'
            else:
                output_fn = fn + '.xbundle'
        self.output_fn = output_fn

        self.fix_filters = [self.fix_xhtml_descriptor_in_p,
                            self.fix_attrib_string,
                            self.add_url_names,
                            self.fix_table,
                            self.fix_latex_minipage_div,
                            self.process_edxcite,
                            self.process_askta,
                            self.process_showhide,
                            self.process_edxxml,
                            self.process_include,
                            self.process_includepy,
                            ]
        if extra_xml_filters:
            self.fix_filters += extra_xml_filters

        self.URLNAMES = []

    def convert(self):
        '''
        Convert xhtml to xbundle and then xbundle to directory of XML files.
        if self.do_merge then do not overwrite course files; attempt to merge them.
        '''
        self.xhtml2xbundle()
        self.xb.save(self.output_fn)
        print "xbundle generated (%s): " % self.output_fn
        tags = ['chapter', 'sequential', 'problem', 'html']
        for tag in tags:
            print "    %s: %d" % (tag, len(self.xb.course.findall('.//%s' % tag)))
        self.xb.export_to_directory(self.output_dir, xml_only=True)
        print "Course exported to %s/" % self.output_dir

        if self.do_merge and self.xb.overwrite_files:
            self.merge_course()

    def merge_course(self):
        print "    merging files %s" % self.xb.overwrite_files
        for fn in self.xb.overwrite_files:
            if str(fn).endswith('course.xml.new'):
                # course.xml shouldn't need merging
                os.unlink(fn)
            else:
                newcourse = etree.parse(open(fn)).getroot()
                oldfn = fn[:-4]
                oldcourse = etree.parse(open(oldfn)).getroot()
                oldchapters = [x.get('url_name') for x in oldcourse]
                newchapters = []
                for chapter in newcourse:
                    if chapter.get('url_name') in oldchapters:
                        continue		# already in old course, skip
                    oldcourse.append(chapter)	# wasn't in old course, move it there
                    newchapters.append(chapter.get('url_name'))
                self.xb.write_xml_file(oldfn, oldcourse, force_overwrite=True)
                os.unlink(fn)
                print "    added new chapters %s" % newchapters

    def xhtml2xbundle(self):
        '''
        Convert XHTML output of PlasTeX to an edX xbundle file.
        Use lxml to parse the XML and extract the desired parts.
        '''
        xml = etree.fromstring(self.xhtml)
        self.xml = xml
        for filter in self.fix_filters:
            filter(xml)
        no_overwrite = ['course'] if self.do_merge else []
        xb = xbundle.XBundle(force_studio_format=True, keep_urls=True,
                             no_overwrite=no_overwrite)
        xb.KeepTogetherTags = ['sequential', 'vertical', 'conditional']
        course = xml.find('.//course')
        if course is not None:
            xb.set_course(course)
        self.xb = xb
        return xb

    @staticmethod
    def fix_table(tree):
        '''
        Force tables to have table-layout: auto, no borders on table data
        '''
        for table in tree.findall('.//table'):
            table.set('style','table-layout:auto')
            for td in table.findall('.//td'):
                newstyle = td.get('style', '')
                if newstyle:
                    newstyle += '; '
                newstyle += 'border:none'
                td.set('style', newstyle)

    @staticmethod
    def fix_latex_minipage_div(tree):
        '''
        latex minipages turn into things like <div style="width:216.81pt" class="minipage">...</div>
        but inline math inside does not render properly.  So change div to text.
        '''
        for div in tree.findall('.//div[@class="minipage"]'):
            div.tag = 'text'
    
    def process_askta(self, tree):
        '''
        add "Ask TA!" links
        arguments are taken as space delimited settings

        if "settings" set, then:
           - save key,value for next uses of edXaskta
           - do not display a link

        examples:

        % sets settings, does not display link
        \edXaskta{settings=1 label="Ask TA!" url_base="htps://edx.org/mycourse" to:"me@example.edu" cc:"ta@example.edu"}

        % displays Email TA link
        \edXaskta{label="Email TA" subject:"help"}
        '''

        special_attribs = [ 'url_base', 'cnt', 'label' ]

        if not hasattr(self, 'askta_data'):
            subject = "Question about {name}"
            body = "This is a question about the problem at COURSE_URL/{url_name}\n\n" 
            self.askta_data = { 'cnt': 0, 'label': 'Ask TA!', 'to':'', 'cc':'', 'subject':subject, 
                                'body': body,
                                'url_base': 'https://edx.org',
            }

        for askta in tree.findall('.//askta'):
            text = askta.text
            args = {}
            if text:
                argset = split_args_with_quoted_strings(text)
                try:
                    args = dict([x.split('=',1) for x in argset])
                    for arg in args:
                        args[arg] = self.stripquotes(args[arg], checkinternal=True)
                except Exception, err:
                    print "Error %s" % err
                    print "Failed in parsing args to edXaskta = %s" % text
                    raise
                if 'settings' in args:
                    args.pop('settings')
                    self.askta_data.update(args)
                    # print "askTA settings updated: %s" % self.askta_data
                    # remove this element from xml tree
                    self.remove_parent_p(askta)
                    p = askta.getparent()
                    p.remove(askta)
                    continue

            # generate button link, something like this:
            #   <input style="float:right" class="check Check" type="button" value="Ask TA!" onclick="SendMail();"/>
            # <script type="text/javascript">
            # var amp = String.fromCharCode(38);
            # function SendMail() {
            #          var link = "mailto:me@example.com"
            #             + "?cc=myCCaddress@example.com"
            #             + amp + "subject=" + escape("This is my subject")
            #             + amp + "body=";
            #          window.open(link,'AskTA', "height=500,width=700");
            # }
            # </script>

            data = {}
            data.update(self.askta_data)
            data.update(args)

            display_name = ''
            url_name = ''
            for parent in askta.xpath('ancestor::*')[::-1]:
                display_name = parent.get('display_name', '')
                if display_name:
                    url_name = parent.get('url_name')
                    break

            data['subject'] = data['subject'].format(name=display_name)
            data['body'] = data['body'].format(url_name=url_name, **data)

            self.askta_data['cnt'] += 1
            smfn = 'SendMail_%d' % self.askta_data['cnt']

            askta.tag = 'span'
            askta.text = ''

            atin = etree.SubElement(askta, 'input')
            atin.set('style', 'float:right')
            atin.set('class', 'check Check')
            atin.set('value', data['label'])
            atin.set('type', 'button')
            atin.set('onclick', '%s();' % smfn)
            
            for attrib in special_attribs:
                data.pop(attrib)

            atlid = 'aturl_%s' % self.askta_data['cnt']
            atlink = etree.SubElement(askta, 'a')
            atlink.set('style', 'display:none')
            atlink.set('href', '/course/jump_to_id')
            atlink.set('id', atlid)

            mailto = 'mailto:%s' % data['to']
            data.pop('to')
            body = data.pop('body')
            mailto += '?' + urllib.urlencode(data)
            mailto += '&' + urllib.urlencode({'body': body})

            jscode = ('\nfunction %s() {\n'
                      '    var cu = encodeURI(window.location.origin + $("#%s").attr("href"));\n'
                      '    var link = "%s";\n'
                      '    link = link.replace("COURSE_URL", cu);\n'
                      '    link = link.replace(/&/g, String.fromCharCode(38));\n'
                      '    console.log(link);\n'
                      '    Logger.log("askta",{link:link});\n'
                      '    window.open(link, "AskTA", "height=500,width=700"); \n'
                      '}') % (smfn, atlid, mailto)

            script = etree.SubElement(askta, 'script')
            script.set('type', 'text/javascript')
            script.text = jscode

    @staticmethod
    def stripquotes(x,checkinternal=False):
        if x.startswith('"') and x.endswith('"'):
            if checkinternal and '"' in x[1:-1]:
                return x
            return x[1:-1]
        if x.startswith("'") and x.endswith("'"):
            return x[1:-1]
        return x

    def process_edxcite(self, tree):
        '''
        Add citation link visible on mouse hoover.
        '''
        if not hasattr(self, 'edxcitenum'):
            self.edxcitenum = 0
        for edxcite in tree.findall('.//edxcite'):
            self.edxcitenum += 1
            ref = edxcite.get('ref', None)
            if ref is None or not ref:
                ref = '[%d]' % self.edxcitenum
            text = edxcite.text
            exc = etree.Element('a')
            edxcite.addnext(exc)
            sup = etree.SubElement(exc, 'sup')
            sup.text = ref
            exc.set('href', '#')
            exc.set('title', text)
            # print "  --> %s" % etree.tostring(exc)
            p = edxcite.getparent()
            p.remove(edxcite)

    @staticmethod
    def remove_parent_p(xml):
        '''
        If xml is inside an otherwise empty <p>, then push it up and remove the <p>
        '''
        p = xml.getparent()
        todrop = xml
        where2add = xml
        if p.tag=='p' and not p.text.strip():	# if in empty <p> then remove that <p>
            todrop = p
            where2add = p
            p = p.getparent()

            # move from xml to parent
            for child in xml:
                where2add.addprevious(child)
                p.remove(todrop)

    def process_edxxml(self, tree):
        '''
        move content of edXxml into body
        If edXxml is within a <p> then drop the <p>.  This allows edXxml to be used for discussion and video.
        '''
        for edxxml in tree.findall('.//edxxml'):
            self.remove_parent_p(edxxml)

    @staticmethod
    def process_showhide(tree):
        for showhide in tree.findall('.//edxshowhide'):
            shid = showhide.get('id')
            if shid is None:
                print "Error: edXshowhide must be given an id argument.  Aborting."
                raise Exception
            print "---> showhide %s" % shid
            #jscmd = "javascript:toggleDisplay('%s','hide','show')" % shid
            jscmd = "javascript:$('#%s').toggle();" % shid
    
            shtable = etree.Element('table')
            showhide.addnext(shtable)
    
            desc = showhide.get('description','')
            shtable.set('class',"wikitable collapsible collapsed")
            shdiv = etree.XML('<tbody><tr><th> %s [<a onclick="%s" href="javascript:void(0);" id="%sl">show</a>]</th></tr></tbody>' % (desc,jscmd,shid))
            shtable.append(shdiv)
    
            tr = etree.SubElement(shdiv,'tr')
            tr.set('id',shid)
            tr.set('style','display:none')
            tr.append(showhide)	# move showhide to become td of table
            showhide.tag = 'td'
            showhide.attrib.pop('id')
            showhide.attrib.pop('description')
    
    @staticmethod
    def process_include(tree, do_python=False):
        '''
        Include XML or python file.

        For python files, wrap inside <script><![CDATA[ ... ]]></script>
        '''
        tag = './/edxinclude'
        cmd = 'edXinclude'
        if do_python:
            tag += 'py'
            cmd += "py"
        for include in tree.findall(tag):
            incfn = include.text
            if incfn is None:
                print "Error: %s must specify file to include!" % cmd
                print "See xhtml source line %s" % getattr(include,'sourceline','<unavailable>')
                raise
            incfn = incfn.strip()
            try:
                incdata = open(incfn).read()
            except Exception, err:
                print "Error %s: cannot open include file %s to read" % (err,incfn)
                print "See xhtml source line %s" % getattr(include,'sourceline','<unavailable>')
                raise
            try:
                if do_python:
                    incxml = etree.fromstring('<script><![CDATA[\n%s\n]]></script>' % incdata)
                else:
                    incxml = etree.fromstring(incdata)
            except Exception, err:
                print "Error %s parsing XML for include file %s" % (err,incfn)
                print "See xhtml source line %s" % getattr(include,'sourceline','<unavailable>')
                raise
    
            print "--> including file %s at line %s" % (incfn,getattr(include,'sourceline','<unavailable>'))
            if incxml.tag=='html' and len(incxml)>0:		# strip out outer <html> container
                for k in incxml:
                    include.addprevious(k)	
            else:
                include.addprevious(incxml)
            p = include.getparent()
            p.remove(include)

    def process_includepy(self, tree):
        self.process_include(tree, do_python=True)

    def add_url_names(self, xml):
        '''
        Generate unique url_name database keys for all XML descriptor tags, for
        which the user did not provide one.  Do this by recursively walking the
        xml tree.
        '''
        #print "add_url_names: %s" % xml.tag
        if xml.tag in self.DescriptorTags:
            if not xml.tag=='course':
                dn = xml.get('display_name', '')
                if not dn:
                    dn = xml.getparent().get('display_name', '') + '_' + xml.tag
                new_un = self.make_url_name(xml.get('url_name', dn), xml.tag)
                if 'url_name' in xml.keys() and not new_un == xml.get('url_name'):
                    print "Warning: url_name %s changed to %s" % (xml.get('url_name'), new_un)
                xml.set('url_name', new_un)
        if not xml.tag in ['problem', 'html']:
            for child in xml:
                self.add_url_names(child)

    def make_url_name(self, s, tag=''):
        '''
        Turn string s into a valid url_name.
        Use tag if provided.
        '''
        map = {'"\':<>': '',
               ',/().;=+ ': '_',
               '/': '__',
               '&': 'and',
               '[': 'LB_',
               ']': '_RB',
               '?# ': '_',
               }
        if not s:
            s = tag
        for m,v in map.items():
            for ch in m:
                s = s.replace(ch,v)
        if s in self.URLNAMES and not s.endswith(tag):
            s = '%s_%s' % (tag, s)
        while s in self.URLNAMES:
            s += 'x'
        self.URLNAMES.append(s)
        return s

    @staticmethod
    def do_attrib_string(elem):
        '''
        parse attribute strings, and add to xml elements.
        attribute strings are space delimited, and optional for elements
        like chapter, sequential, vertical, text
        '''
        attrib_string = elem.get('attrib_string','')
        if attrib_string:
            attrib_list=split_args_with_quoted_strings(attrib_string)    
            if len(attrib_list)==1 & len(attrib_list[0].split('='))==1: # a single number n is interpreted as weight="n"
                elem.set('weight',attrib_list[0]) 
            else: # the normal case, can remove backwards compatibility later if desired
                for s in attrib_list: 
                    attrib_and_val = s.split('=')
                    if len(attrib_and_val) != 2:
                        print "ERROR! the attribute list '%s' for element %s is not properly formatted" % (attrib_string, elem.tag)
                        # print "attrib_and_val=%s" % attrib_and_val
                        print etree.tostring(elem)
                        sys.exit(-1)
                    elem.set(attrib_and_val[0],attrib_and_val[1].strip("\"")) # remove extra quotes
        if 'attrib_string' in elem.keys():
            elem.attrib.pop('attrib_string') # remove attrib_string

    def fix_attrib_string(self, xml):
        '''
        Convert attrib_string in <problem>, <chapter>, etc. to attributes, intelligently.
        '''
        TAGS = ['problem', 'chapter', 'sequential', 'vertical', 'course', 'html']
        for tag in TAGS:
            for elem in xml.findall('.//%s' % tag):
                self.do_attrib_string(elem)

    def fix_xhtml_descriptor_in_p(self, xml):
        '''
        Sometimes have <sequential><p><problem>...</problem></p></sequential>
        Have to remove contaiing <p>
        This happens for problem, chapter, sequential, html, any DescriptorTag
        '''
        for tag in self.DescriptorTags:
            for elem in xml.findall('.//%s' % tag):
                parent = elem.getparent()
                if parent.tag=='p':
                    parent.addprevious(elem)		# move <problem> up before <p>
                    parent.getparent().remove(parent)	# remove the <p>
    

def CommandLine():
    parser = optparse.OptionParser(usage="usage: %prog [options] filename.tex",
                                   version="%prog 1.0")
    parser.add_option('-v', '--verbose', 
                      dest='verbose', 
                      default=False, action='store_true',
                      help='verbose error messages')
    parser.add_option("-o", "--output-xbundle",
                      action="store",
                      dest="output_fn",
                      default="",
                      help="Filename for output xbundle file",)
    parser.add_option("-d", "--output-directory",
                      action="store",
                      dest="output_dir",
                      default="course",
                      help="Directory name for output course XML files",)
    parser.add_option("-c", "--config-file",
                      action="store",
                      dest="config_file",
                      default="latex2edx_config",
                      help="configuration file to load",)
    parser.add_option("-m", "--merge-chapters",
                      action="store_true",
                      dest="merge",
                      default=False,
                      help="merge chapters into existing course directory",)
    (opts, args) = parser.parse_args()

    if len(args)<1:
        parser.error('wrong number of arguments')
        sys.exit(0)
    fn = args[0]

    config = DEFAULT_CONFIG
    # load local configuration file if available
    if os.path.exists(opts.config_file):
        lc = __import__(opts.config_file, fromlist=['local_config'])
        config.update(lc.local_config)

    c = latex2edx(fn, verbose=opts.verbose, output_fn=opts.output_fn,
                  output_dir=opts.output_dir,
                  do_merge=opts.merge,
        )
    c.convert()
    
