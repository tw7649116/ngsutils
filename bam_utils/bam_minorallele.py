#!/usr/bin/env python
"""
Calculates a minor allele frequency in pooled genomic sequencing.

Given a BAM file and a genomic reference, for each position covered in the 
BAM file, show the reference base, the potential minor allele, and probable
background.

This assumes that if a SNP exists, there is likely only one possible variation.
So, this calculation will fail if more than one minor allele is present.  This
also ignores indels.

If rpy2 is installed and -alleles is given, this will also calculate a 95% CI.

The results are saved to a SQLite database that is created if it doesn't exist.
"""

import os,sys,math,subprocess,sqlite3
from support.eta import ETA
import pysam

class Blackhole(object):
    def write(self, string):
        pass

__sink = Blackhole()

try:
    __rsrc = os.path.join(os.path.dirname(__file__),'minorallele_cpci.R')
    import rpy2.robjects as robjects
    with open(__rsrc) as f:
        robjects.r(f.read())
except Exception:
    robjects = None
    rscript = os.path.join(os.path.dirname(__file__),'minorallele_cpci.rsh')
    if not os.path.exists(rscript):
        sys.stderr.write('Missing R script: %s\n' % rscript)
        sys.exit(-1)
        
    stdout = sys.stdout
    sys.stdout = __sink
    retval = subprocess.call(rscript)
    sys.stdout = stdout
    if retval != 0:
        sys.stderr.write('Error calling R script: %s\n' % rscript)
        sys.exit(-1)
    

def usage():
    base = os.path.basename(sys.argv[0])
    print __doc__
    print """
Usage: %s {opts} in.bam ref.fa output.db

Arguments:
  in.bam        BAM files to import
  ref.fa        Genomic reference sequence (indexed FASTA)
  output.db     The SQLite database to use

Options:
  -name name    Sample name (default to filename)
  -qual val     Minimum quality level to use in calculations
                (numeric, Sanger scale) (default 0)

  -count val    Only report bases with a minimum coverage of {val}
                (default 0)

  -alleles val  The number of alleles included in this sample
                If given, a Clopper-Pearson style confidence interval will be 
                calculated. (requires rpy2)
""" % (base)
    if robjects:
        print "rpy2 detected!"
    else:
        print "rpy2 not detected!"
    
    sys.exit(1)

def connect_db(fname):
    create = False
    if not os.path.exists(fname):
        create = True

    conn = sqlite3.connect(fname)
    if create:
        conn.executescript('''
CREATE TABLE samples (
id INTEGER PRIMARY KEY,
name TEXT UNIQUE,
alleles INTEGER
);

CREATE TABLE calls (
sample_id INTEGER,
chrom TEXT,
pos INTEGER,
ref TEXT,
alt TEXT,
total_count INTEGER,
ref_count INTEGER,
alt_count INTEGER,
background_count INTEGER,
ref_back INTEGER,
alt_back INTEGER,
ci_low REAL,
ci_high REAL,
allele_count_low REAL,
allele_count_high REAL,
FOREIGN KEY (sample_id) REFERENCES samples(id),
PRIMARY KEY(sample_id,chrom,pos)
);

CREATE INDEX call_pos ON calls (chrom,pos);

''')
    conn.commit()
    return conn


def bam_minorallele(bam_fname,ref_fname,conn,min_qual=0, min_count=0, num_alleles = 0,name = None):
    bam = pysam.Samfile(bam_fname,"rb")
    ref = pysam.Fastafile(ref_fname)
    eta = ETA(0,bamfile=bam)
    
    if not name:
        name = os.path.basename(bam_fname)

    sample_id = None
    conn.execute('INSERT INTO samples (name,alleles) VALUES (?,?)', (name,num_alleles))
    conn.commit()
    for row in conn.execute('SELECT id FROM samples WHERE name = ?', (name,)):
        sample_id = row[0]
    
    try:
        assert sample_id
    except:
        return
    
    printed = False
    for pileup in bam.pileup():
        chrom = bam.getrname(pileup.tid)
        eta.print_status(extra='%s:%s' % (chrom,pileup.pos),bam_pos=(pileup.tid,pileup.pos))
        
        counts = {'A':0,'C':0,'G':0,'T':0}
        inserts = 0
        deletions = 0
        total = 0
        
        for pileupread in pileup.pileups:
            if not pileupread.is_del:
                if min_qual:
                    if pileupread.alignment.qual[pileupread.qpos] < min_qual:
                        continue
                if pileupread.indel == 0:
                    base = pileupread.alignment.seq[pileupread.qpos].upper()
                    if base != 'N':
                        counts[base]+=1
                        total += 1
        
        if total > min_count:
            refbase = ref.fetch(chrom,pileup.pos,pileup.pos+1).upper()
            if not refbase in counts:
                continue
            
            refcount = counts[refbase]
            
            # sort non-ref counts.  first is alt, next is background
            
            scounts = []
            for c in counts:
                if c != refbase:
                    scounts.append((counts[c],c))
                    
            scounts.sort()
            scounts.reverse()
            
            altbase = scounts[0][1]
            altcount = scounts[0][0]
            background = scounts[1][0]
            
            refback = refcount-background
            altback = altcount-background

            if num_alleles:
                ci_low,ci_high = calc_cp_ci(refback+altback,altback,num_alleles)
                allele_low = ci_low * num_alleles
                allele_high = ci_high * num_alleles
            else:
                ci_low, ci_high, allele_low, allele_high = 0

            if not math.isnan(ci_low) and ci_low > 0.0:
                args = (sample_id, chrom, pileup.pos+1, refbase, altbase, total, refcount, altcount, background, refback, altback, ci_low, ci_high, allele_low, allele_high)
                conn.execute('INSERT INTO calls (sample_id,chrom,pos,ref,alt,total_count,ref_count,alt_count,background_count,ref_back,alt_back,ci_low,ci_high,allele_count_low,allele_count_high) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', args)

    conn.commit()
    eta.done()
    bam.close()
    ref.close()

__ci_cache={}
def calc_cp_ci(N,count,num_alleles):
    if (N,count,num_alleles) in __ci_cache:
        return __ci_cache[(N,count,num_alleles)]

    vals = (float('nan'),float('nan'))

    if robjects:
        stdout = sys.stdout
        sys.stdout = __sink
        vals = robjects.r['CP.CI'](N,count,num_alleles)
        sys.stdout = stdout
    else:
        vals = [float(x) for x in subprocess.Popen([str(x) for x in [rscript,N,count,num_alleles]],stdout=subprocess.PIPE).communicate()[0].split()]

    __ci_cache[(N,count,num_alleles)] = vals
    return vals

if __name__ == '__main__':
    bam = None
    ref = None
    db = None
    
    min_qual = 0
    min_count = 0
    num_alleles = 0
    name = None
    
    last = None
    for arg in sys.argv[1:]:
        if last == '-qual':
            min_qual = int(arg)
            last = None
        elif last == '-count':
            min_count = int(arg)
            last = None
        elif last == '-alleles':
            num_alleles = int(arg)
            last = None
        elif last == '-name':
            name = arg
            last = None
        elif arg == '-h':
            usage()
        elif arg in ['-qual','-count','-alleles','-name']:
            last = arg
        elif not bam and os.path.exists(arg) and os.path.exists('%s.bai' % arg):
            bam = arg
        elif not ref and os.path.exists(arg) and os.path.exists('%s.fai' % arg):
            ref = arg
        elif not db:
            db = arg
        else:
            print "Unknown option or missing index: %s" % arg
            usage()

    if not bam or not ref or not db:
        usage()
    else:
        conn = connect_db(db)
        bam_minorallele(bam,ref,conn,min_qual,min_count,num_alleles,name)
        conn.close()
