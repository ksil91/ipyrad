#!/usr/bin/env python2.7

""" 
de-replicates edit files and clusters de-replciated reads 
by sequence similarity using vsearch
"""

from __future__ import print_function
# pylint: disable=E1101
# pylint: disable=F0401
# pylint: disable=W0142
# pylint: disable=W0212

import os
import sys
import gzip
#import shlex
import tempfile
import itertools
import subprocess
import numpy as np
from collections import OrderedDict
from .rawedit import comp

import logging
LOGGER = logging.getLogger(__name__)



def cleanup(data, sample):
    """ stats, cleanup, and sample """
    
    ## get clustfile
    sample.files.clusters = os.path.join(data.dirs.clusts,
                                         sample.name+".clustS.gz")
    sample.files.database = os.path.join(data.dirs.clusts,
                                         sample.name+".catg")

    if "pair" in data.paramsdict["datatype"]:
        ## record merge file name temporarily
        sample.files.merged = os.path.join(data.dirs.edits,
                                           sample.name+"_merged_.fastq")
        ## record how many read pairs were merged
        with open(sample.files.merged, 'r') as tmpf:
            ## divide by four b/c its fastq
            sample.stats.reads_merged = len(tmpf.readlines())/4

    ## get depth stats
    infile = gzip.open(sample.files.clusters)
    duo = itertools.izip(*[iter(infile)]*2)
    depth = []
    thisdepth = []
    while 1:
        try:
            itera = duo.next()[0]
        except StopIteration:
            break
        if itera != "//\n":
            thisdepth.append(int(itera.split(";")[-2][5:]))
        else:
            ## append and reset
            depth.append(sum(thisdepth))
            thisdepth = []
    infile.close()

    if depth:
        ## make sense of stats
        depth = np.array(depth)
        keepmj = depth[depth >= data.paramsdict["mindepth_majrule"]]    
        keepstat = depth[depth >= data.paramsdict["mindepth_statistical"]]

        ## sample assignments
        sample.stats["state"] = 3
        sample.stats["clusters_total"] = len(depth)
        sample.stats["clusters_hidepth"] = max([len(i) for i in \
                                             (keepmj, keepstat)])
        sample.depths = depth

        data._stamp("s3 clustering on "+sample.name)        
    else:
        print("no clusters found for {}".format(sample.name))

    ## Get some stats from the bam files
    ## This is moderately hackish. samtools flagstat returns
    ## the number of reads in the bam file as the first element
    ## of the first line, this call makes this assumption.
    if not data.paramsdict["assembly_method"] == "denovo":
        cmd = data.samtools+\
            " flagstat "+sample.files.unmapped_reads
        result = subprocess.check_output(cmd, shell=True,
                                              stderr=subprocess.STDOUT)
        sample.stats["refseq_unmapped_reads"] = int(result.split()[0])

        cmd = data.samtools+\
            " flagstat "+sample.files.mapped_reads
        result = subprocess.check_output(cmd, shell=True,
                                              stderr=subprocess.STDOUT)
        sample.stats["refseq_mapped_reads"] = int(result.split()[0])



def muscle_align(args):
    """ aligns reads, does split then aligning for paired reads """
    ## parse args
    data, chunk = args

    ## data are already chunked, read in the whole thing
    infile = open(chunk, 'rb')
    clusts = infile.read().split("//\n//\n")
    out = []
    #indels = np.zeros((len(clusts), 225), dtype=np.int32)

    ## iterate over clusters and align
    for clust in clusts:
        stack = []
        lines = clust.split("\n")
        names = lines[::2]
        seqs = lines[1::2]

        ## append counter to end of names b/c muscle doesn't retain order
        names = [j+str(i) for i, j in enumerate(names)]        

        ## don't bother aligning singletons
        if len(names) <= 1:
            if names:
                stack = [names[0]+"\n"+seqs[0]]
        else:
            ## split seqs if paired end seqs
            if all(["ssss" in i for i in seqs]):
                seqs1 = [i.split("ssss")[0] for i in seqs] 
                seqs2 = [i.split("ssss")[1] for i in seqs]

                string1 = muscle_call(data, names[:200], seqs1[:200])
                string2 = muscle_call(data, names[:200], seqs2[:200])
                anames, aseqs1 = parsemuscle(string1)
                anames, aseqs2 = parsemuscle(string2)

                ## resort so they're in same order
                aseqs = [i+"ssss"+j for i, j in zip(aseqs1, aseqs2)]
                somedic = OrderedDict()
                for i in range(len(anames)):
                    ## filter for max indels
                    #allindels = aseqs.count('-')
                    intindels = aseqs[i].rstrip('-').lstrip('-').count('-')
                    somedic[anames[i]] = aseqs[i]
                    if intindels > sum(data.paramsdict["max_Indels_locus"]):
                        LOGGER.info("high indels: %s", aseqs[i])
                        #indels[x] = 1

            else:
                string1 = muscle_call(data, names[:200], seqs[:200])
                anames, aseqs = parsemuscle(string1)
                somedic = OrderedDict()
                for i in range(len(anames)):
                    ## filter for max internal indels
                    somedic[anames[i]] = aseqs[i]                       
                    intindels = aseqs[i].rstrip('-').lstrip('-').count('-')
                    if intindels > sum(data.paramsdict["max_Indels_locus"]):
                        LOGGER.info("high indels: %s", aseqs[i])

            for key in somedic.keys():
                stack.append(key+"\n"+somedic[key])
            #for key in keys:
            #    if key[-1] == '-':
            #        ## reverse matches should have --- overhang
            #        if set(somedic[key][:4]) == {"-"}:
            #            stack.append(key+"\n"+somedic[key])
            #        else:
            #            pass ## excluded
            #    else:
            #        stack.append(key+"\n"+somedic[key])

        if stack:
            out.append("\n".join(stack))

    ## write to file after
    infile.close()
    outfile = open(chunk, 'wb')#+"_tmpout_"+str(num))
    outfile.write("\n//\n//\n".join(out)+"\n")#//\n//\n")
    outfile.close()



# def sortalign(stringnames):
#     """ parses muscle output from a string to two lists """
#     objs = stringnames.split("\n>")
#     seqs = [i.split("\n")[0].replace(">", "")+"\n"+\
#               "".join(i.split('\n')[1:]) for i in objs]
              
#     aligned = [i.split("\n") for i in seqs]
#     newnames = [">"+i[0] for i in aligned]
#     seqs = [i[1] for i in aligned]     
#     ## return in sorted order by names
#     sortedtups = [(i, j) for i, j in zip(*sorted(zip(newnames, seqs), 
#                                          key=lambda pair: pair[0]))]
#     return sortedtups


def parsemuscle(out):
    """ parse muscle string output into two sorted lists """
    lines = out[1:].split("\n>")
    names = [line.split("\n", 1)[0] for line in lines]
    seqs = [line.split("\n", 1)[1].replace("\n", "") for line in lines]
    tups = zip(names, seqs)
    #LOGGER.debug("TUPS TUPS TUPS %s", tups)
    ## who knew, zip(*) is the inverse of zip
    anames, aseqs = zip(*sorted(tups, 
                        key=lambda x: int(x[0].split(";")[-1][1:])))
    return anames, aseqs



def muscle_call(data, names, seqs):
    """ makes subprocess call to muscle. A little faster than before """
    inputstring = "\n".join(">"+i+"\n"+j for i, j in zip(names, seqs))
    return subprocess.Popen(data.bins.muscle, 
                            stdin=subprocess.PIPE, 
                            stdout=subprocess.PIPE)\
                            .communicate(inputstring)[0]



def build_clusters(data, sample):
    """ combines information from .utemp and .htemp files 
    to create .clust files, which contain un-aligned clusters """

    ## derepfile 
    derepfile = os.path.join(data.dirs.edits, sample.name+"_derep.fastq")

    ## vsearch results files
    ufile = os.path.join(data.dirs.clusts, sample.name+".utemp")
    htempfile = os.path.join(data.dirs.clusts, sample.name+".htemp")

    ## create an output file to write clusters to        
    clustfile = gzip.open(os.path.join(
                          data.dirs.clusts,
                          sample.name+".clust.gz"), 'wb')
    sample.files["clusts"] = clustfile

    ## if .u files are present read them in as userout
    if not os.path.exists(ufile):
        LOGGER.error("no .utemp file found for %s", sample.name)
        sys.exit("no .utemp file found for sample {}".format(sample.name))
    userout = open(ufile, 'rb').readlines()

    ## load derep reads into a dictionary
    hits = {}  
    ioderep = open(derepfile, 'rb')
    dereps = itertools.izip(*[iter(ioderep)]*2)
    for namestr, seq in dereps:
        _, count = namestr.strip()[:-1].split(";size=")
        hits[namestr] = [int(count), seq.strip()]
    ioderep.close()

    ## create dictionary of .utemp cluster hits
    udic = {} 
    for uline in userout:
        uitems = uline.strip().split("\t")
        ## if the seed is in udic
        if ">"+uitems[1]+"\n" in udic:
            ## append hit to udict
            udic[">"+uitems[1]+'\n'].append([">"+uitems[0]+"\n", 
                                            uitems[4],
                                            uitems[5].strip(),
                                            uitems[3]])
        else:
            ## write as seed
            udic[">"+uitems[1]+'\n'] = [[">"+uitems[0]+"\n", 
                                         uitems[4],
                                         uitems[5].strip(),
                                         uitems[3]]]

    ## map sequences to clust file in order
    seqslist = [] 
    for key, values in udic.iteritems():
        seq = [key.strip()+"*\n"+hits[key][1]]

        ## allow only 6 internal indels in hits to seed for within-sample clust
        if not any([int(i[3]) > 6 for i in values]):
            for i in xrange(len(values)):
                if values[i][1] == "+":
                    ## only match forward reads if high Cov
                    #if cov[i] >= 90:
                    seq.append(values[i][0].strip()+"+\n"+\
                               hits[values[i][0]][1])
                else:
                    ## name change for reverse hits
                    ## allow low Cov for reverse hits
                    ## .replace("_r1;", "_c1;")+\
                    ## reverse matches should have left terminal gaps
                    ## if not it is a bad alignment and exclude
                    revseq = comp(hits[values[i][0]][1][::-1])
                    seq.append(values[i][0].strip()+"-\n"+revseq)
        seqslist.append("\n".join(seq))
    clustfile.write("\n//\n//\n".join(seqslist)+"\n")

    ## make Dict. from seeds (_temp files) 
    iotemp = open(htempfile, 'rb')
    invars = itertools.izip(*[iter(iotemp)]*2)
    seedsdic = {k:v for (k, v) in invars}  
    iotemp.close()

    ## create a set for keys in I not in seedsdic
    set1 = set(seedsdic.keys())   ## htemp file (no hits) seeds
    set2 = set(udic.keys())       ## utemp file (with hits) seeds
    diff = set1.difference(set2)  ## seeds in 'temp not matched to in 'u
    if diff:
        for i in list(diff):
            clustfile.write("//\n//\n"+i.strip()+"*\n"+hits[i][1]+'\n')
    #clustfile.write("//\n//\n\n")
    clustfile.close()
    del dereps
    del userout
    del udic
    del seedsdic




def split_among_processors(data, samples, ipyclient, noreverse, force):
    """ pass the samples to N engines to execute run_full on each.

    :param data: An Assembly object
    :param samples: one or more samples selected from data
    :param noreverse: toggle revcomp clustering despite datatype default
    :param threaded_view: ipyparallel load_balanced_view client

    :returns: None
    """
    ## nthreads per job for clustering. Find optimal splitting.
    ncpus = len(ipyclient.ids)  
    
    threads_per = 1
    if ncpus >= 4:
        threads_per = 2
    if ncpus >= 8:
        threads_per = 4
    if ncpus > 20:
        threads_per = ncpus/4

    ## we could create something like the following when there are leftovers:
    ## [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10]]

    threaded_view = ipyclient.load_balanced_view(
                    targets=ipyclient.ids[::threads_per])
    tpp = len(threaded_view)

    ## make output folder for clusters  
    data.dirs.clusts = os.path.join(
                        os.path.realpath(data.paramsdict["working_directory"]),
                        data.name+"_"+
                       "clust_"+str(data.paramsdict["clust_threshold"]))
    if not os.path.exists(data.dirs.clusts):
        os.makedirs(data.dirs.clusts)

    ## submit files and args to queue, for func clustall
    submitted_args = []
    for sample in samples:
        if force:
            submitted_args.append([data, sample, noreverse, tpp])
        else:
            ## if not already clustered/mapped
            if sample.stats.state <= 2.5:
                submitted_args.append([data, sample, noreverse, tpp])
            else:
                ## clustered but not aligned
                pass

    # If reference sequence is specified then try read mapping, else pass.
    if not data.paramsdict["assembly_method"] == "denovo":
        ## make output directory for read mapping process
        data.dirs.refmapping = os.path.join(
                        os.path.realpath(data.paramsdict["working_directory"]),
                        data.name+"_refmapping")
      
        if not os.path.exists(data.dirs.refmapping):
            os.makedirs(data.dirs.refmapping)
        ## Set the mapped and unmapped reads files for this sample
        for sample in samples:
            sorted_unmapped_bamhandle = os.path.join(data.dirs.refmapping,
                                            sample.name+"-sorted-unmapped.bam")
            sorted_mapped_bamhandle = os.path.join(data.dirs.refmapping, 
                                            sample.name+"-sorted-mapped.bam")
            sample.files.unmapped_reads = sorted_unmapped_bamhandle
            sample.files.mapped_reads = sorted_mapped_bamhandle

        ## call to ipp for read mapping
        results = threaded_view.map(mapreads, submitted_args)
        try:
            results.get()
        except (AttributeError, TypeError):
            for key in ipyclient.history:
                if ipyclient.metadata[key].error:
                    LOGGER.error("step3 readmapping error: %s", 
                        ipyclient.metadata[key].error)
                    raise SystemExit("step3 readmapping error.\n({})."\
                                     .format(ipyclient.metadata[key].error))
                if ipyclient.metadata[key].stdout:
                    LOGGER.error("step3 readmapping stdout:%s", 
                        ipyclient.metadata[key].stdout)
                    raise SystemExit("step3 readmapping error.")
            LOGGER.error(ipyclient.metadata)
            sys.exit("")


    ## DENOVO calls
    results = threaded_view.map(clustall, submitted_args)
    try:
        results.get()
        clusteredsamples = [i[1] for i in submitted_args]
        for sample in clusteredsamples:
            sample.state = 2.5

    except (AttributeError, TypeError):
        for key in ipyclient.history:
            if ipyclient.metadata[key].error:
                LOGGER.error("step3 clustering error: %s", 
                    ipyclient.metadata[key].error)
                raise SystemExit("step3 clustering error.\n({})."\
                                 .format(ipyclient.metadata[key].error))
            if ipyclient.metadata[key].stdout:
                LOGGER.error("step3 clustering stdout:%s", 
                    ipyclient.metadata[key].stdout)
                raise SystemExit("step3 clustering error.")
    del threaded_view 

    ## call to ipp for aligning
    #lbview = ipyclient.load_balanced_view()
    for sample in samples:
        if sample.state < 3:
            multi_muscle_align(data, sample, ipyclient)
    #del lbview

    ## If reference sequence is specified then pull in alignments from 
    ## mapped bam files and write them out to the clustS files to fold
    ## them back into the pipeline.
    if not data.paramsdict["assembly_method"] == "denovo":
        ## make output directory for read mapping process
        data.dirs.refmapping = os.path.join(
                        os.path.realpath(data.paramsdict["working_directory"]),
                        data.name+"_refmapping")
        for sample in samples:
            results = getalignedreads(data, sample)

    ## write stats to samples
    for sample in samples:
        cleanup(data, sample)

    ## summarize results to stats file
    ## TODO: update statsfile with mapped and unmapped reads for reference mapping
    data.statsfiles.s3 = os.path.join(data.dirs.clusts, "s3_cluster_stats.txt")
    if not os.path.exists(data.statsfiles.s3):
        with open(data.statsfiles.s3, 'w') as outfile:
            outfile.write(""+\
            "{:<20}   {:>9}   {:>9}   {:>9}   {:>9}   {:>9}   {:>9}\n""".\
                format("sample", "N_reads", "clusts_tot", 
                       "clusts_hidepth", "avg.depth.tot", 
                       "avg.depth>mj", "avg.depth>stat"))

    ## append stats to file
    outfile = open(data.statsfiles.s3, 'a+')
    samples.sort(key=lambda x: x.name)
    for sample in samples:
        outfile.write(""+\
    "{:<20}   {:>9}   {:>10}   {:>11}   {:>13.2f}   {:>11.2f}   {:>13.2f}\n".\
                      format(sample.name, 
                             int(sample.stats["reads_filtered"]),
                             int(sample.stats["clusters_total"]),
                             int(sample.stats["clusters_hidepth"]),
                             np.mean(sample.depths),
                             np.mean(sample.depths[sample.depths >= \
                                     data.paramsdict["mindepth_majrule"]]),
                             np.mean(sample.depths[sample.depths >= \
                                     data.paramsdict["mindepth_statistical"]])
                             ))
    outfile.close()



def combine_pairs(data, sample):
    """ in prep """
    #LOGGER.info("in combine_pairs: %s", sample.files.edits)

    ## open file for writing to
    combined = os.path.join(data.dirs.edits, sample.name+"_pairs.fastq")
    combout = open(combined, 'wb')

    ## read in paired end read files"
    ## create iterators to sample 4 lines at a time
    fr1 = open(sample.files.nonmerge1, 'rb')
    quart1 = itertools.izip(*[iter(fr1)]*4)
    fr2 = open(sample.files.nonmerge2, 'rb')
    quart2 = itertools.izip(*[iter(fr2)]*4)
    quarts = itertools.izip(quart1, quart2)

    ## a list to store until writing
    writing = []
    counts = 0

    ## iterate until done
    while 1:
        try:
            read1s, read2s = quarts.next()
        except StopIteration:
            break
        writing.append("\n".join([
                        read1s[0].strip(),
                        read1s[1].strip()+\
                            "ssss"+read2s[1].strip(),
                        read1s[2].strip(),
                        read1s[3].strip()+\
                            "ssss"+read2s[3].strip()]
                        ))
        counts += 1
        if not counts % 1000:
            combout.write("\n".join(writing)+"\n")
            writing = []
    if writing:
        combout.write("\n".join(writing)+"\n")
        combout.close()

    sample.files.edits = [(combined, )]
    sample.files.pairs = combined
    return sample



def merge_fastq_pairs(data, sample):
    """ Merge paired fastq reads. """
    #LOGGER.info("merging reads")

    ## tempnames for merge files
    sample.files.merged = os.path.join(data.dirs.edits,
                          sample.name+"_merged_.fastq")
    sample.files.nonmerge1 = os.path.join(data.dirs.edits, 
                              sample.name+"_nonmerged_R1_.fastq")
    sample.files.nonmerge2 = os.path.join(data.dirs.edits, 
                              sample.name+"_nonmerged_R2_.fastq")
    sample.files.rc = os.path.join(data.dirs.edits, 
                              sample.name+"_revcomp_R2_.fastq")

    try:
        maxn = sum(data.paramsdict['max_low_qual_bases'])
    except TypeError:
        maxn = data.paramsdict['max_low_qual_bases']
    minlen = str(max(32, data.paramsdict["filter_min_trim_len"]))

    assert os.path.exists(sample.files.edits[0][1]), \
           "No paired read file (_R2_ file) found."

    ## vsearch revcomp R2
    cmd = data.bins.vsearch \
      +" --fastx_revcomp "+sample.files.edits[0][1] \
      +" --fastqout "+sample.files.rc
    LOGGER.warning(cmd)
    try:
        subprocess.check_call(cmd, shell=True,   
                                   stderr=subprocess.STDOUT,
                                   stdout=subprocess.PIPE)
    except subprocess.CalledProcessError as inst:
        LOGGER.error(subprocess.STDOUT)
        LOGGER.error(cmd)        
        sys.exit("Error in merging pairs: \n({}).".format(inst))

    ## vsearch merging

    cmd = data.bins.vsearch \
      +" --fastq_mergepairs "+sample.files.edits[0][0] \
      +" --reverse "+sample.files.rc \
      +" --fastqout "+sample.files.merged \
      +" --fastqout_notmerged_fwd "+sample.files.nonmerge1 \
      +" --fastqout_notmerged_rev "+sample.files.nonmerge2 \
      +" --fasta_width 0 " \
      +" --fastq_allowmergestagger " \
      +" --fastq_minmergelen "+minlen \
      +" --fastq_maxns "+str(maxn) \
      +" --fastq_minovlen 12 " \
      +" --fastq_maxdiffs 4 "
    LOGGER.warning(cmd)
    try:
        subprocess.check_call(cmd, shell=True,   
                                   stderr=subprocess.STDOUT,
                                   stdout=subprocess.PIPE)
    except subprocess.CalledProcessError as inst:
        LOGGER.error(subprocess.STDOUT)
        LOGGER.error(cmd)        
        sys.exit("Error in merging pairs: \n({}).".format(inst))
    finally:
        pass
        ## kill unfinished files if they exist

    return sample



def concat_edits(data, sample):
    """ concatenate if multiple edits files for a sample """
    ## if more than one tuple in the list
    if len(sample.files.edits) > 1:
        ## create temporary concat file
        tmphandle1 = os.path.join(data.dirs.edits,
                              "tmp1_"+sample.name+".concat")       
        with open(tmphandle1, 'wb') as tmp:
            for editstuple in sample.files.edits:
                with open(editstuple[0]) as inedit:
                    tmp.write(inedit)

        if 'pair' in data.paramsdict['datatype']:
            tmphandle2 = os.path.join(data.dirs.edits,
                                "tmp2_"+sample.name+".concat")       
            with open(tmphandle2, 'wb') as tmp:
                for editstuple in sample.files.edits:
                    with open(editstuple[1]) as inedit:
                        tmp.write(inedit)
            sample.files.edits = [(tmphandle1, tmphandle2)]
        else:
            sample.files.edits = [(tmphandle1, )]
    return sample



def derep_and_sort(data, sample, nthreads):
    """ dereplicates reads and sorts so reads that were highly
    replicated are at the top, and singletons at bottom, writes
    output to derep file """

    ## reverse complement clustering for some types    
    if "gbs" in data.paramsdict["datatype"]:
        reverse = " -strand both "
    else:
        reverse = " "

    #LOGGER.debug("derep FILE %s", sample.files.edits[0][0])

    ## do dereplication with vsearch
    cmd = data.bins.vsearch+\
          " -derep_fulllength "+sample.files.edits[0][0]\
         +reverse \
         +" -output "+os.path.join(data.dirs.edits, sample.name+"_derep.fastq")\
         +" -sizeout " \
         +" -threads "+str(nthreads) \
         +" -fasta_width 0"

    ## run vsearch
    try:
        subprocess.call(cmd, shell=True,
                             stderr=subprocess.STDOUT,
                             stdout=subprocess.PIPE)
    except subprocess.CalledProcessError as inst:
        LOGGER.error(cmd)
        LOGGER.error(inst)
        sys.exit("Error in vsearch: \n{}\n{}\n{}."\
                 .format(inst, subprocess.STDOUT, cmd))



def cluster(data, sample, noreverse, nthreads):
    """ calls vsearch for clustering. cov varies by data type, 
    values were chosen based on experience, but could be edited by users """
    ## get files
    derephandle = os.path.join(data.dirs.edits, sample.name+"_derep.fastq")
    uhandle = os.path.join(data.dirs.clusts, sample.name+".utemp")
    temphandle = os.path.join(data.dirs.clusts, sample.name+".htemp")

    ## datatype variables
    if data.paramsdict["datatype"] == "gbs":
        reverse = " -strand both "
        cov = " -query_cov .35 " 
    elif data.paramsdict["datatype"] == 'pairgbs':
        reverse = "  -strand both "
        cov = " -query_cov .60 " 
    else:  ## rad, ddrad
        reverse = " -leftjust "
        cov = " -query_cov .90 "

    ## override reverse clustering option
    if noreverse:
        reverse = " -leftjust "
        LOGGER.warn(noreverse, "not performing reverse complement clustering")

    ## get call string
    cmd = data.bins.vsearch+\
        " -cluster_smallmem "+derephandle+\
        reverse+\
        cov+\
        " -id "+str(data.paramsdict["clust_threshold"])+\
        " -userout "+uhandle+\
        " -userfields query+target+id+gaps+qstrand+qcov"+\
        " -maxaccepts 1"+\
        " -maxrejects 0"+\
        " -minsl 0.5"+\
        " -fulldp"+\
        " -threads "+str(nthreads)+\
        " -usersort "+\
        " -notmatched "+temphandle+\
        " -fasta_width 0"

    ## run vsearch
    try:
        subprocess.call(cmd, shell=True,
                             stderr=subprocess.STDOUT,
                             stdout=subprocess.PIPE)
    except subprocess.CalledProcessError as inst:
        sys.exit("Error in vsearch: \n{}\n{}".format(inst, subprocess.STDOUT))



def multi_muscle_align(data, sample, ipyclient):
    """ Splits the muscle alignment across nthreads processors, each runs on 
    1000 clusters at a time. This is a kludge until I find how to write a 
    better wrapper for muscle. 
    """
    ## create loaded 
    lbview = ipyclient.load_balanced_view()

    ## split clust.gz file into nthreads*10 bits cluster bits
    tmpnames = []
    #LOGGER.debug("aligning %s", sample.name)

    try: 
        ## get the number of clusters
        clustfile = os.path.join(data.dirs.clusts, sample.name+".clust.gz")
        clustio = gzip.open(clustfile, 'rb')
        optim = 1000

        ## write optim clusters to each tmp file
        inclusts = iter(clustio.read().strip().split("//\n//\n"))
        grabchunk = list(itertools.islice(inclusts, optim))
        while grabchunk:
            with tempfile.NamedTemporaryFile('w+b', 
                                             delete=False, 
                                             dir=data.dirs.clusts,
                                             prefix=sample.name+"_", 
                                             suffix='.ali') as out:
                out.write("//\n//\n".join(grabchunk))
                #out.write("\n")
            tmpnames.append(out.name)
            grabchunk = list(itertools.islice(inclusts, optim))
    
        ## create job queue
        submitted_args = []
        for fname in tmpnames:
            submitted_args.append([data, fname])

        ## run muscle on all tmp files            
        results = lbview.map_async(muscle_align, submitted_args)
        results.get()

        ## concatenate finished reads
        sample.files.clusters = os.path.join(data.dirs.clusts,
                                             sample.name+".clustS.gz")
        with gzip.open(sample.files.clusters, 'wb') as out:
            for fname in tmpnames:
                with open(fname) as infile:
                    out.write(infile.read()+"//\n//\n")

    except Exception as inst:
        LOGGER.warn(inst)
        raise

    finally:
        ## still delete tmpfiles if job was interrupted
        for fname in tmpnames:
            if os.path.exists(fname):
                os.remove(fname)



def clustall(args):
    """ Running on remote Engine. Refmaps, then merges, then dereplicates, 
    then denovo clusters reads. """

    ## get args
    data, sample, noreverse, nthreads = args

    ## concatenate edits files in case a Sample has multiple, and 
    ## returns a new Sample.files.edits with the concat file
    sample = concat_edits(data, sample)

    ## merge fastq pairs
    if 'pair' in data.paramsdict['datatype']:
        ## merge pairs that overlap into a merge file
        sample = merge_fastq_pairs(data, sample)
        ## pair together remaining pairs into a pairs file
        sample = combine_pairs(data, sample)
        ## if merge file, append to pairs file
        with open(sample.files.pairs, 'a') as tmpout:
            with open(sample.files.merged, 'r') as tmpin:
                tmpout.write(tmpin.read().replace("_c1\n", "_m4\n"))
        #LOGGER.info(sample.files.edits)

    ## convert fastq to fasta, then derep and sort reads by their size
    #LOGGER.debug(data, sample, nthreads)
    derep_and_sort(data, sample, nthreads)
    #LOGGER.debug(data, sample, nthreads)
    
    ## cluster derep fasta files in vsearch 
    cluster(data, sample, noreverse, nthreads)

    ## cluster_rebuild
    build_clusters(data, sample)



def mapreads(args):
    """ Attempt to map reads to reference sequence. This reads in the 
    samples.files.edits .fasta files, and attempts to map each read to the 
    reference sequences. Unmapped reads are dropped right back in the de novo 
    pipeline. Reads that map successfully are processed and pushed downstream 
    and joined with the rest of the data post musle_align. The read mapping 
    produces a sam file, unmapped reads are pulled out of this and dropped back 
    in place of the edits (.fasta) file. The raw edits .fasta file is moved to 
    .<sample>.fasta to hide it in case the mapping screws up and we need to roll-back.
    Mapped reads stay in the sam file and are pulled out of the pileup later."""

    ## TODO: Right now this is coded to read in the edits which are .fasta format
    ## It would be ideal to read in the fastq instead, since this contains info
    ## about base quality scores, improves the mapping confidence.

    ## get args
    data, sample, noreverse, nthreads = args

    ## Files we'll use during reference sequence mapping
    ##
    ## samhandle - Raw output from smalt reference mapping
    ## unmapped_bamhandle - bam file containing only unmapped reads
    ## mapped_bamhandle - bam file with only successfully mapped reads
    ##    In the case of paired end, we only consider pairs where both
    ##    reads map successfully
    ## sorted_*_bamhandle - Sorted bam files for mapped and unmapped
    ##    This is a precursor to bam2fq which requires sorted bams
    ## unmapped_fastq_handle - The final output file of this function
    ##    writes out unmapped reads to the 'edits' directory as .fq
    ##    which is what downstream analysis expects
    samhandle = os.path.join(data.dirs.refmapping, sample.name+".sam")
    unmapped_bamhandle = os.path.join(data.dirs.refmapping, sample.name+"-unmapped.bam")
    mapped_bamhandle = os.path.join(data.dirs.refmapping, sample.name+"-mapped.bam")
    sorted_unmapped_bamhandle = sample.files["unmapped_reads"]
    sorted_mapped_bamhandle = sample.files["mapped_reads"]

    ## TODO, figure out why edits is a tuple? There could be multiple edits files, yes?
    ## but by the time we get to step three they are all collapsed in to one big file
    ## which is the first element of the tuple, yes?
    ##
    ## TODO: This is hackish, we are overwriting the fastq that contains all reads
    ## might want to preserve the full .fq file in case of a later 'force' command
    unmapped_fastq_handle = sample.files.edits[0][0] #os.path.join(data.dirs.edits, sample.name+".fastq")

################
# TODO: Paired end isn't handled yet, but it needs to be.
    ## datatype variables
    if data.paramsdict["datatype"] in ['gbs']:
        reverse = " -strand both "
        cov = " -query_cov .35 "
    elif data.paramsdict["datatype"] in ['pairgbs', 'merged']:
        reverse = "  -strand both "
        cov = " -query_cov .60 "
    else:  ## rad, ddrad, ddradmerge
        reverse = " -leftjust "
        cov = " -query_cov .90 "

    ## override reverse clustering option
    if noreverse:
            reverse = " -leftjust "
            print(noreverse, "not performing reverse complement clustering")
################


    ## get call string
    cmd = data.smalt+\
        " map -f sam -n " + str(nthreads) +\
        " -o " + samhandle +\
        " " + data.paramsdict['reference_sequence'] +\
        " " + sample.files.edits[0][0]

    ## run smalt
    subprocess.call(cmd, shell=True,
                         stderr=subprocess.STDOUT,
                         stdout=subprocess.PIPE)

    ## Get the reads that map successfully. For PE both reads must map
    ## successfully in order to qualify.
    ##   1) Get mapped reads and convert to bam
    ##   2) Sort them and save the path to the bam to sample.files.mapped_reads
    ## The mapped reads are synced back into the pipeline downstream, after
    ## muscle aligns the umapped reads.
    ##
    ## samtools view arguments
    ##   -b = write to .bam
    ##   -F = Select all reads that DON'T have this flag (0x4 means unmapped)
    ##        <TODO>: This is deeply hackish right now, it will need some
    ##                serious thinking to make this work for PE, etc.
    cmd = data.samtools+\
        " view -b -F 0x4 "+samhandle+\
            " > " + mapped_bamhandle
    subprocess.call(cmd, shell=True,
                         stderr=subprocess.STDOUT,
                         stdout=subprocess.PIPE)
    ## Step 2 sort mapped bam
    ##   -T = Temporary file name, this is required by samtools, you can ignore it
    ##        Here we just hack it to be samhandle.tmp cuz samtools will clean it up
    ##   -O = Output file format, in this case bam
    ##   -o = Output file name
    cmd = data.samtools+\
        " sort -T "+samhandle+".tmp" +\
        " -O bam "+mapped_bamhandle+\
        " -o "+sorted_mapped_bamhandle
    subprocess.call(cmd, shell=True,
                         stderr=subprocess.STDOUT,
                         stdout=subprocess.PIPE)

    ##############################################
    ## Do unmapped
    ##############################################

    ## Fetch up the unmapped reads and write them to the edits .fasta file
    ## In order to do this we have to go through a little process:
    ##   1) Get the unmapped reads and convert to bam
    ##   2) Sort them 
    ##   3) Dump bam to fastq

    ## Step 1 get unmapped reads with samtools view
    ##   -b = write out to .bam
    ##   -f = Select only reads with associated flags set (0x4 means unmapped)
    cmd = data.samtools+\
        " view -b -f 0x4 "+samhandle+\
            " > " + unmapped_bamhandle
    subprocess.call(cmd, shell=True,
                         stderr=subprocess.STDOUT,
                         stdout=subprocess.PIPE)

    ## Step 2 sort unmapped bam
    ##   -T = Temporary file name, this is required by samtools, you can ignore it
    ##        Here we just hack it to be samhandle.tmp cuz samtools will clean it up
    ##   -O = Output file format, in this case bam
    ##   -o = Output file name
    cmd = data.samtools+\
        " sort -T "+samhandle+".tmp" +\
        " -O bam "+unmapped_bamhandle+\
        " -o "+sorted_unmapped_bamhandle
    subprocess.call(cmd, shell=True,
                         stderr=subprocess.STDOUT,
                         stdout=subprocess.PIPE)

    ## Step 3 Dump sorted bam to fastq
    ## No args for this one. Pipe it through gzip, because the downstream
    ## analysis expects gzipped fq
    cmd = data.samtools+\
        " bam2fq "+sorted_unmapped_bamhandle+\
        " | gzip > "+unmapped_fastq_handle
    subprocess.call(cmd, shell=True,
                         stderr=subprocess.STDOUT,
                         stdout=subprocess.PIPE)

    ## bs for experimentation. is samtools/smalt fscking the output?
    ## TODO: get rid of this when mapped reads get returned to the 
    ## pipeline right. or whenever, it's not useful.
    cmd = data.samtools+\
        " bam2fq "+sorted_mapped_bamhandle+\
        " | gzip >> "+unmapped_fastq_handle
    subprocess.call(cmd, shell=True,
                         stderr=subprocess.STDOUT,
                         stdout=subprocess.PIPE)

    ## This is the end of processing for each sample. Stats
    ## are appended to the sample for mapped and unmapped reads 
    ## during cluster_within.cleanup()

    ## This is hax to get fastq to fasta to get this off the ground.
    ## samtools bam2fq natively returns fastq, you just delete this code
    ## when fastq pipleline is working
#    writing = []
#    with open(os.path.realpath(unmapped_fastq_handle), 'rb') as fq:
#        quart1 = itertools.izip(*[iter(fq)]*4)
#        quarts = itertools.izip(quart1, iter(int, 1))
#        writing = []
#        j=0
#        while 1:
#            try:
#                quart = quarts.next()
#            except StopIteration:
#                break
#            read1 = [i.strip() for i in quart[0]]
#            sseq = ">"+sample.name+"_"+str(j)+\
#                           "_r1\n"+read1[1]+"\n"
#            writing.append(sseq)
#            j = j+1
#
#    with open( sample.files.edits[0], 'w' ) as out:
#        out.write("".join(writing))
    #####################################################################
    ## End block of dummy code, delete this when fastq works


def getalignedreads(data, sample):
    """Pull aligned reads out of sorted mapped bam files and
    append them to the clustS.gz file so the fall into downstream analysis """

    mapped_fastq_handle = "/tmp/wat.fq"
    ## Build the samtools bam2fq command to push bam out
    cmd = data.samtools+\
        " bam2fq "+sample.files["mapped_reads"]+\
        " > "+mapped_fastq_handle
    subprocess.call(cmd, shell=True,
                         stderr=subprocess.STDOUT,
                         stdout=subprocess.PIPE)



def run(data, samples, noreverse, force, ipyclient):
    """ run the major functions for clustering within samples """

    ## list of samples to submit to queue
    subsamples = []

    ## if sample is already done skip
    for _, sample in samples:
        if not force:
            if sample.stats.state >=3:
                print("  Skipping {}; aleady clustered.".\
                      format(sample.name))
            else:
                if sample.stats.reads_filtered:
                    subsamples.append(sample)
        else:
            ## force to overwrite
            if sample.stats.reads_filtered:            
                subsamples.append(sample)

    ## run subsamples 
    if not subsamples:
        print("  No Samples ready to be clustered. First run step2().")
    else:
        args = [data, subsamples, ipyclient, noreverse, force]
        split_among_processors(*args)



if __name__ == "__main__":
    ## test...
    import ipyrad as ip

    ## reload autosaved data. In case you quit and came back 
    data1 = ip.load.load_assembly("test_rad/data1.assembly")

    ## run step 6
    data1.step3(force=True)

    # DATA = Assembly("test")
    # DATA.get_params()
    # DATA.set_params(1, "./")
    # DATA.set_params(28, '/Volumes/WorkDrive/ipyrad/refhacking/MusChr1.fa')
    # DATA.get_params()
    # print(DATA.log)
    # DATA.step3()
    #PARAMS = {}
    #FASTQS = []
    #QUIET = 0
    #run(PARAMS, FASTQS, QUIET)
    pass
    
