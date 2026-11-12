"""Shared evidence helpers. No semantic or listening pass is inferred from a score."""
from __future__ import annotations
import hashlib
import json
import math
import os
import re
from pathlib import Path

VERSION = '1.0'
HABITS = ('然后呢','就是说','所以说','这个','那个','然后','就是','一个','其实','反正','对吧','嗯','呃')
CHECKS = ('meaning_duplicates','sentence_complete','logical_flow','references_clear','title_supported')

def norm(s): return ''.join(c for c in str(s).lower() if c.isalnum())
def signature(value): return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def save(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(tmp,path)
def clock(t):
    ms=round(max(0,t)*1000);h,r=divmod(ms,3600000);m,r=divmod(r,60000);s,ms=divmod(r,1000)
    return f'{h:02}:{m:02}:{s:02}.{ms:03}'

def word_chars(words):
    """All characters retain the FULL containing ASR token interval. Never interpolate."""
    return [(c,float(w['start']),float(w['end'])) for w in words for c in norm(w.get('word',w.get('text','')))]

def habits(words):
    chars=word_chars(words);text=''.join(c[0] for c in chars);out=[];i=0
    boundaries={float(w['start']) for w in words};ends={float(w['end']) for w in words}
    while i<len(text):
        h=next((h for h in HABITS if text.startswith(h,i)),None)
        if not h:i+=1;continue
        j=i+len(h);a,b=chars[i][1],chars[j-1][2]
        exact=(i==0 or chars[i-1][2]<=a) and (j==len(chars) or chars[j][1]>=b)
        before=a-chars[i-1][2] if i else 0;after=chars[j][1]-b if j<len(chars) else 0
        repeated=text.startswith(h,j) or (i>=len(h) and text[i-len(h):i]==h)
        normal=h in ('这个','那个','一个') and j<len(text) and after<.15 and not repeated
        out.append({'text':h,'start':a,'end':b,'exact_token_boundary':exact,
                    'candidate':bool(exact and not normal and (repeated or before>=.15 or after>=.15)),
                    'note':'词内命中，仅供定位，不能据此切词' if not exact else '结合语境回听，不是自动删除'})
        i=j
    return out

def dense_habits(words):
    found=habits(words);out=[];last=-100
    for item in found:
        t=item['start'];window=[h for h in found if t<=h['start']<t+10]
        if len(window)>3 and t-last>=10:
            out.append({'start':t,'end':t+10,'count':len(window),'hits':window});last=t
    return out

def quiet_point(levels,step,lo,hi,default):
    """Search only in caller-proven non-word intervals."""
    if not levels or hi-lo<step:return max(lo,min(hi,default))
    a=max(0,math.ceil(lo/step));b=min(len(levels)-1,math.floor(hi/step))
    if b<a:return max(lo,min(hi,default))
    low=min(levels[a:b+1]);runs=[];start=None
    for i in range(a,b+2):
        if i<=b and levels[i]<=low+2:
            if start is None:start=i
        elif start is not None:runs.append((start,i-1));start=None
    x,y=max(runs,key=lambda v:(v[1]-v[0],-abs((v[0]+v[1])*step/2-default)))
    return max(lo,min(hi,(x+y)*step/2))

def safe_ranges(start,end,silences,words,cuts=(),levels=None,step=.01,compress=True,keep_max=.6,tail=.2,head=.15):
    """Explicit cuts are exact; only proven quiet gaps are shortened automatically.
    Untranscribed voiced sounds are review candidates, never classified as coughs here.
    """
    if not (0<=head<=2 and 0<=tail<=2 and .3<=keep_max<=10 and head+tail<=keep_max):raise ValueError('停顿参数无效')
    ws=sorted((float(a),float(b)) for a,b in words if b>start and a<end)
    cuts=sorted((float(a),float(b)) for a,b in cuts)
    for a,b in cuts:
        if not start<=a<b<=end:raise ValueError('删减区间越界')
        if any(x+1e-6<a<y-1e-6 or x+1e-6<b<y-1e-6 for x,y in ws):raise ValueError('删减落在真实词内部，请回听或整词取舍')
    retained=[(a,b) for a,b in ws if not any(x<=a and b<=y for x,y in cuts)]
    if compress:
        for x,y in silences:
            x=max(start,float(x));y=min(end,float(y))
            if y-x<=keep_max:continue
            a=x+tail;b=y-head
            if x<=start+.015:a=start
            if y>=end-.015:b=end
            if b<=a or any(p-.04<b and q+.04>a for p,q in retained):continue
            cuts.append((a,b))
    parts=[(start,end)]
    for x,y in sorted(cuts):
        nxt=[]
        for a,b in parts:
            if y<=a or x>=b:nxt.append((a,b));continue
            if a<x:nxt.append((a,x))
            if y<b:nxt.append((y,b))
        parts=nxt
    out=[]
    for a,b in parts:
        inside=[(x,y) for x,y in retained if x<b and y>a]
        if compress and inside:
            first,last=inside[0][0],inside[-1][1]
            # No search crosses speech. No fixed padding can enter a deleted neighbour.
            if first>a+.04:a=quiet_point(levels,step,max(a,first-.15),first-.015,max(a,first-.06))
            if last<b-.04:b=quiet_point(levels,step,last+.015,min(b,last+.15),min(b,last+.08))
        if b-a>=.02:out.append((a,b))
    return out

def frame_bounds(a,b,words,fps,lower,upper,blocked=()):
    """Quantize without cutting an included token or reaching a removed neighbour.
    Expansion is less than one frame and only into non-word, non-deleted space.
    """
    lo=math.ceil(a*fps-1e-7);hi=math.floor(b*fps+1e-7);inner_lo,inner_hi=lo,hi
    inside=[(x,y) for x,y in words if x<b-1e-7 and y>a+1e-7]
    if inside:
        lo=min(lo,math.floor(min(x for x,y in inside)*fps+1e-7))
        hi=max(hi,math.ceil(max(y for x,y in inside)*fps-1e-7))
    def clear(lo,hi):
        x,y=lo/fps,hi/fps
        return not(x<lower-1e-6 or y>upper+1e-6 or any(min(y,v)-max(x,u)>1e-6 for u,v in blocked))
    if not clear(lo,hi):
        # Whisper 的词时间常与被删的邻句首尾相接，且不在帧上：向外取整必进删除区。
        # 只在向外扩过的一侧向内收一帧（削掉不到一帧的估计字头/字尾），绝不带回已删声音。
        for l2,h2 in ((lo+(lo<inner_lo),hi),(lo,hi-(hi>inner_hi)),(lo+(lo<inner_lo),hi-(hi>inner_hi))):
            if h2>l2 and clear(l2,h2):lo,hi=l2,h2;break
    if not clear(lo,hi):
        raise ValueError('这处词边界没有足够的安全视频帧；请回听后选邻近静音、另一遍完整表达，或保留较完整的原句。不要靠微调数字切掉字头或带回已删声音。')
    return lo,hi

def review_bundle(rows, extra=None):
    """Rows are ACTUALLY retained captions, grouped by original source unit in final order."""
    merged=[]
    for row in rows:
        if merged and row.get('key')==merged[-1].get('key'):
            merged[-1]['text']+=' '+row['text'];merged[-1]['end']=row['end']
        else:merged.append(dict(row))
    pairs=[]
    grams=[{norm(r['text'])[i:i+2] for i in range(len(norm(r['text']))-1)} for r in merged]
    for i,a in enumerate(merged):
        ta=norm(a['text'])
        if len(ta)<6:continue
        for j in range(i+1,len(merged)):
            b=merged[j];tb=norm(b['text']);score=len(grams[i]&grams[j])/max(1,len(grams[i]|grams[j]))
            if len(tb)>=6 and (score>=.32 or ta in tb or tb in ta):
                pid='D'+signature([a['key'],a['text'],b['key'],b['text']])[:12]
                pairs.append({'id':pid,'a':i,'b':j,'score':round(score,3)})
    value={'rows':merged,'pairs':pairs,'extra':extra or {},'schema_version':1}
    value['signature']=signature(value)
    return value

def write_review(folder,rows,extra=None):
    folder=Path(folder);bundle=review_bundle(rows,extra)
    if (folder/'review-result.json').exists():
        old=json.loads((folder/'review-result.json').read_text(encoding='utf-8'))
        save(folder/'review-history'/((old.get('decisions',{}).get('signature') or signature(old))+'.json'),old)
    save(folder/'review-result.json',{'content_review':'awaiting_content_review','signature':bundle['signature'],'listening':{'status':'not_done'}})
    save(folder/'review.json',bundle)
    parts=[];group=[];chars=0
    for row in bundle['rows']:
        if group and (row['end']-group[0]['start']>480 or chars+len(row['text'])>4000):
            parts.append(group);group=[];chars=0
        group.append(row);chars+=len(row['text'])
    if group:parts.append(group)
    index=['# 成片文稿阅读索引','','按顺序读完所有分份，记录每份新增信息，再做跨份同义重复和逻辑复查。只读局部不能填 read_all。','']
    for i,part in enumerate(parts,1):
        file=folder/'review-parts'/f'{i:04d}.md';file.parent.mkdir(parents=True,exist_ok=True)
        body=[f"# 成片文稿 {i}/{len(parts)}",'']+[f"- [{clock(r['start'])}] {r['key']}：{r['text']}" for r in part]
        file.write_text('\n'.join(body)+'\n',encoding='utf-8')
        index.append(f"- [{i} · {clock(part[0]['start'])}–{clock(part[-1]['end'])}]({file.relative_to(folder).as_posix()})")
    (folder/'成片文稿阅读索引.md').write_text('\n'.join(index)+'\n',encoding='utf-8')
    lines=['# 成片复查','','词面相似度只提醒，不能发现所有同义改述。必须通读全部成片文稿，判断重复、递进、半句话、指代与衔接。',
           '声音复测和再转写也只是线索；咳嗽、尾音、吞字需回听具体位置。','', '## 疑似重复','']
    for p in bundle['pairs']:
        a,b=[bundle['rows'][p[k]] for k in ('a','b')]
        lines.append(f"- {p['id']}：{a['key']}「{a['text']}」 ↔ {b['key']}「{b['text']}」")
    if not bundle['pairs']:lines.append('- 粗筛未命中；仍须检查不同表述的同义重复。')
    lines+=['','## 最终保留的文稿','']
    lines += [f"- [{clock(r['start'])}] {r['key']}：{r['text']}" for r in bundle['rows']]
    lines+=['','## 自动复测记录','', '```json',json.dumps(extra or {},ensure_ascii=False,indent=2),'```','',
            '## 记录复查','', '填写 review-decisions.json：signature 原样复制，read_all 为实际读完，checks 的五项逐一判断；每个 D 编号写 keep 及理由，或 revise 及下一步。修改后重新生成，旧签名自动失效。',
            '通过内容复查不等于通过听审。最多两轮自动返修后，仍有不确定项就交待审稿并指出位置；不填假通过。']
    (folder/'成片复查.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    template={'signature':bundle['signature'],'read_all':False,'checks':{k:False for k in CHECKS},
              'pairs':{p['id']:{'action':'revise','reason':''} for p in bundle['pairs']},'listening':{'status':'not_done','evidence':''}}
    save(folder/'review-decisions-template.json',template)
    return {'review':str(folder/'成片复查.md'),'review_json':str(folder/'review.json'),'status':'awaiting_content_review','pairs':len(bundle['pairs'])}

def record_review(folder,decisions):
    folder=Path(folder);bundle=json.loads((folder/'review.json').read_text(encoding='utf-8'));d=json.loads(Path(decisions).read_text(encoding='utf-8'))
    if d.get('signature')!=bundle['signature']:raise ValueError('成片已改变，重新通读最新文稿，不能复用旧复查')
    ids={p['id'] for p in bundle['pairs']};pairs=d.get('pairs',{})
    if set(pairs)!=ids:raise ValueError('每个疑似重复编号都需要判断；请从本轮模板填写')
    for p in pairs.values():
        if p.get('action') not in ('keep','revise') or not str(p.get('reason','')).strip():raise ValueError('重复疑点需 keep/revise 和具体理由')
    passed=d.get('read_all') is True and all(d.get('checks',{}).get(k) is True for k in CHECKS) and all(p['action']=='keep' for p in pairs.values())
    listen=d.get('listening',{})
    if listen.get('status')=='done' and not str(listen.get('evidence','')).strip():raise ValueError('听审需要实际记录')
    result={'content_review':'recorded_pass' if passed else 'needs_revision','listening':listen,'decisions':d}
    save(folder/'review-result.json',result)
    (folder/'复查结论.md').write_text('# 复查结论\n\n'+('内容复查已记录通过。' if passed else '仍有内容问题待处理。')+'\n\n听审：'+listen.get('status','not_done')+'\n',encoding='utf-8')
    return result

def compare_asr(captions,result,where):
    import difflib
    tokens=[]
    for s in result.get('segments',[]):
        for w in s.get('words') or []:tokens += [(c,float(w['start'])) for c in norm(w.get('word',w.get('text','')))]
    want=''.join(norm(c['text']) for c in captions);have=''.join(c for c,t in tokens);issues=[]
    if want and not have:return {'status':'failed','issues':[{'kind':'no_recognized_words','note':'有保留文字但复转写无词结果，不能当通过'}]}
    for op,a,b,x,y in difflib.SequenceMatcher(None,want,have,autojunk=False).get_opcodes():
        if op=='equal':continue
        t=tokens[min(x,len(tokens)-1)][1] if tokens else 0
        issues.append(dict(where(t),kind='asr_difference',expected=want[a:b],heard=have[x:y],note='识别差异，需回听；不据此自动扩大剪点'))
    ws=[w for s in result.get('segments',[]) for w in s.get('words',[])]
    for d in dense_habits(ws):issues.append(dict(where(d['start']),kind='dense_habits',count=d['count']))
    return {'status':'measured_not_listened','issues':issues}

def _recheck_cache_dir():
    import os
    return Path(os.environ.get('CY_EDIT_RECHECK_CACHE') or Path.home()/'.cy-edit'/'recheck-cache')

def retranscribe(preview,folder,transcribe,chunk=480,key=None):
    """Bounded ASR chunks with overlap, real word timestamps retained for localization.
    key（后端+模型+语言）给出时，按“key + 分块 PCM 字节”缓存：返修只改了后半段，前面没变的分块不再重新识别。"""
    import wave,hashlib
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);segments=[]
    with wave.open(str(preview),'rb') as src:
        rate=src.getframerate();total=src.getnframes()/rate
        for index in range(math.ceil(total/chunk)):
            a=index*chunk;b=min(total,a+chunk);lo=max(0,a-.5);hi=min(total,b+.5)
            path=folder/f'chunk-{index:05d}.wav';src.setpos(round(lo*rate))
            with wave.open(str(path),'wb') as dest:
                dest.setparams(src.getparams());dest.writeframes(src.readframes(round((hi-lo)*rate)))
            cached=None
            if key:
                h=hashlib.sha256(key.encode()+b'\0'+path.read_bytes()).hexdigest();cached=_recheck_cache_dir()/h[:2]/(h+'.json')
            if cached and cached.is_file():
                try:result=json.loads(cached.read_text(encoding='utf-8'))
                except (OSError,ValueError):result=transcribe(path)
            else:result=transcribe(path)
            if cached and not cached.is_file():
                try:cached.parent.mkdir(parents=True,exist_ok=True);save(cached,result)
                except OSError:pass
            save(folder/f'chunk-{index:05d}.json',result)
            for seg in result.get('segments',[]):
                words=[]
                for w in seg.get('words') or []:
                    x=float(w['start'])+lo;y=float(w['end'])+lo
                    if a<=(x+y)/2<b:words.append(dict(w,start=x,end=y))
                if words:segments.append({'start':words[0]['start'],'end':words[-1]['end'],'text':''.join(w.get('word',w.get('text','')) for w in words),'words':words})
    return {'segments':segments,'duration_seconds':total,'source':'actual_edited_pcm','chunk_seconds':chunk}
