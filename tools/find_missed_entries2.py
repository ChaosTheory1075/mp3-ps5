import struct,sys,bisect,collections
from capstone import *
from capstone.ppc import *
S=sys.argv[1]
d=open('private-image/image.bin','rb').read(); B=0x82000000; CS,CE=0x821F0000,0x8311C4F4
funcs=sorted(int(x,16) for x in open(S+'/funcs.txt').read().split())
# plus entries already in the config
import re
for m in re.finditer(r'"0x([0-9A-F]{8})"',open('mp3-ps5/mp3-recomp/mp3_config.toml').read()): funcs.append(int(m.group(1),16))
funcs=sorted(set(funcs)); fs=set(funcs)
W=struct.unpack('>%dI'%(len(d)//4),d[:len(d)//4*4]); w=lambda a:W[(a-B)//4]
def sx(v,b): return v-(1<<b) if v&(1<<(b-1)) else v
def owner(v):
    k=bisect.bisect_right(funcs,v)-1; return funcs[k],(funcs[k+1] if k+1<len(funcs) else CE)
WIDE=len(sys.argv)>2
HELP=(0x82FF9600,0x82FFD000)
def orphan(v):
    if v in fs or not(CS<=v<CE) or v%4 or HELP[0]<=v<HELP[1]: return False
    x=w(v)
    if x==0 or x>>24 in(1,2): return False
    q=v-4
    while w(q) in (0,0x60000000) and q>v-64: q-=4   # alignment padding
    p=w(q)
    if WIDE and (p>>26)==18 and (p&3)==1: return True   # after a call that doesn't return
    if (p>>26)==18 and (p&3)==0:
        f,n=owner(v); t=(v-4)+sx(p&0x3FFFFFC,26); return not(f<=t<n)
    return p in(0x4E800420,0x4E800020)
# branch targets inside the image (a candidate reached by a branch is a label)
btgt=set()
for i,x in enumerate(W):
    a=B+i*4
    if not(CS<=a<CE): continue
    op=x>>26
    if op==18 and not(x&2): btgt.add(a+sx(x&0x3FFFFFC,26))
    elif op==16 and not(x&2): btgt.add(a+sx(x&0xFFFC,16))
md=Cs(CS_ARCH_PPC,CS_MODE_32|CS_MODE_BIG_ENDIAN); md.detail=True; md.skipdata=True
src=collections.defaultdict(set)
for i,v in enumerate(W):
    a=B+i*4
    if not(CS<=a<CE) and orphan(v):
        f,n=owner(v)
        if any(f<W[j]<n and not orphan(W[j]) for j in(i-1,i+1)): continue
        src[v].add('data')
# lis rX,hi ... addi/ori rY,rX,lo  (rX tracked until rewritten or the block ends)
LOGIC31={28,60,124,284,316,412,444,476,24,536,792,824,26,954,922,986,27,539,794,826,827,58}
STORE31={151,215,407,149,663,727,983,918,662,150,214,231,135,167,199,0,32,4,68,86,54,278,246,1014,470,982,854,598,19,144,146,178,210,242,467}
UPD31={183,247,439,181,695,759,55,119,311,375,53,565,631,567,631}
def writes(x):
    op=x>>26; rd=(x>>21)&31; ra=(x>>16)&31
    if op in(7,8,12,13,14,15,32,34,40,42,46,48,50,58): return {rd}|({ra} if op==58 and x&3==1 else set())
    if op in(33,35,41,43,49,51): return {rd,ra}
    if op in(37,39,45,53,55): return {ra}
    if op==62: return {ra} if x&3==1 else set()
    if op in(20,21,23,24,25,26,27,28,29,30): return {ra}
    if op==31:
        xo=(x>>1)&0x3FF
        if xo in LOGIC31: return {ra}
        if xo in UPD31: return {ra}|({rd} if xo in(55,119,311,375,53) else set())
        if xo in STORE31: return set()
        return {rd}
    return set()
for i,x in enumerate(W):
    a=B+i*4
    if not(CS<=a<CE) or x>>26!=15 or (x>>16)&31: continue
    hi=(x&0xFFFF)<<16
    if not(0x821E0000<=hi<=0x83120000): continue
    rd=(x>>21)&31
    for k in range(1,400):
        b=a+4*k
        if b>=CE: break
        j=w(b); op=j>>26
        if op in(14,24) and (j>>16)&31==rd:
            v=(hi+sx(j&0xFFFF,16))&0xFFFFFFFF if op==14 else hi|(j&0xFFFF)
            f,n=owner(v)
            if orphan(v) and not(f<=a<n) and v not in btgt: src[v].add('code')
        if rd in writes(j): break
        if j in(0x4E800020,0x4E800420) or (op==18 and not(j&1)): break
out=sorted(src)
print(len(out),collections.Counter(tuple(sorted(s)) for s in src.values()))
open(S+('/missing3.txt' if WIDE else '/missing2.txt'),'w').write('\n'.join('%08X %s'%(v,','.join(sorted(src[v]))) for v in out))
