#!/usr/bin/env python
'''
Truncates each read seq/qual values to be a maximum length
'''

import os,sys

from fastq_utils import read_fastq,is_colorspace_fastq

def fastq_truncate(fname,max_len):
    cs = is_colorspace_fastq(fname)
    
    for name,seq,qual in read_fastq(fname):
        if cs and seq[0] in "atcgATGC":
            sys.stdout.write('%s\n%s\n+\n%s\n' % (name,seq[:max_len+1],qual[:max_len]))
        else:
            sys.stdout.write('%s\n%s\n+\n%s\n' % (name,seq[:max_len],qual[:max_len]))

def usage():
    print __doc__
    print """Usage: %s filename.fastq{.gz} max_len""" % os.path.basename(sys.argv[0])
    sys.exit(1)

if __name__ == '__main__':
    fname = None
    maxlen = 0

    for arg in sys.argv[1:]:
        if not fname and os.path.exists(arg):
            fname = arg
        else:
            maxlen = int(arg)

    if not fname or not maxlen:
        usage()
        
    fastq_truncate(fname,maxlen)