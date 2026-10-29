"""List function entries codegen missed (merged into the function before them).

    find_missed_entries.py <dir with funcs.txt>   (run from the folder with private-image/image.bin)

funcs.txt: the generated sub_ addresses (from symbols.txt.gz), one hex per line.
A candidate is a code address referenced from data, or built in code by lis +
addi/ori, that follows an unconditional tail call out of its owner function, a bctr or a blr
(not jump tables or a switch's case base, which point into their own function).
Output: <dir>/missing.txt; add each as "0x..." = {} under [functions].
"""
import struct,sys,bisect,collections
S=sys.argv[1]
d=open('private-image/image.bin','rb').read(); B=0x82000000; CS,CE=0x821F0000,0x8311C4F4
funcs=sorted(int(x,16) for x in open(S+'/funcs.txt').read().split()); fs=set(funcs)
HELPERS=(0x82FF9600,0x82FFD000)
W=struct.unpack('>%dI'%(len(d)//4),d[:len(d)//4*4])
w=lambda a:W[(a-B)//4]
def sx(v,b): return v-(1<<b) if v&(1<<(b-1)) else v
def btarget(a,i): return a+sx((i&0x3FFFFFC),26)
def owner(v):
    k=bisect.bisect_right(funcs,v)-1; return funcs[k], (funcs[k+1] if k+1<len(funcs) else CE)
def is_entry(v):
    if v in fs or not(CS<=v<CE) or v%4: return False
    if w(v)>>24 in (1,2): return False   # an import thunk (its record words)
    p=w(v-4)
    if (p>>26)==18 and (p&3)==0:   # b (no link): must be a tail call out of the owner
        f,n=owner(v); t=btarget(v-4,p)
        return not(f<=t<n)
    if p in (0x4E800420, 0x4E800020):  # bctr / blr (not inside the save/restore helpers)
        return not(HELPERS[0]<=v<HELPERS[1])
    return False
src=collections.defaultdict(set)
for i,v in enumerate(W):
    a=B+i*4
    if not(CS<=a<CE) and is_entry(v):
        # a jump table: a neighbouring word points into the same function
        f,n=owner(v)
        # (a vtable's neighbours are function starts or entries themselves)
        if any(f<W[j]<n and not is_entry(W[j]) for j in (i-1,i+1) if 0<=j<len(W)): continue
        src[v].add('data')
# lis/addi(or ori) in code
for a in range(CS,CE-4,4):
    i=w(a)
    if i>>26!=15 or (i>>16)&31: continue   # lis rD (addis rD,0,imm)
    rd=(i>>21)&31; hi=(i&0xFFFF)<<16
    for k in range(1,12):
        j=w(a+4*k); op=j>>26
        if op in(14,24) and (j>>16)&31==rd:
            v=(hi+sx(j&0xFFFF,16))&0xFFFFFFFF if op==14 else hi|(j&0xFFFF)
            # a switch's case base is built inside its own function
            if is_entry(v) and not(owner(v)[0]<=a<owner(v)[1]): src[v].add('code')
            break
        if (j>>21)&31==rd and op not in(36,38,44,32,34,40,48,50,52,54): break
# not code: data words in the text section
for bad in (0x830003E8,0x83108CD4): src.pop(bad,None)
out=sorted(src)
print(len(out), collections.Counter(tuple(sorted(s)) for s in src.values()))
open(S+'/missing.txt','w').write('\n'.join('%08X %s'%(v,','.join(sorted(src[v]))) for v in out))
