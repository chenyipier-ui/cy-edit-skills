"""Local PCM evidence, energy measurements, conservative pause suggestions and sample-accurate previews."""
from __future__ import annotations
import math
import os
import re
import sys
import wave
from pathlib import Path
from lve_common import digest, read_json, require, run, write_json
from lve_sources import quick_fingerprint

RATE=16000
FILLERS={'嗯','呃','唔','额','嗯嗯'}


def ensure_audio(root,source,config):
    original=source['original']
    require(quick_fingerprint(original['path'])==original['fingerprint'],'原片改变，不能复用分析音频')
    recipe={'source':original['identity'],'audio':source['audio_stream_index'],'origin':original['start_seconds'],'version':3}
    folder=Path(root)/'analysis'/source['id']/digest(recipe)[:20];folder.mkdir(parents=True,exist_ok=True)
    path=folder/'audio.wav';stamp=folder/'audio.json'
    if path.exists() and stamp.exists():
        stored=read_json(stamp)
        if stored.get('recipe')==recipe and stored.get('fingerprint')==quick_fingerprint(path):return path
    part=folder/'audio.partial.wav';total=original['duration_seconds']
    if source['audio_stream_index'] is None:
        with wave.open(str(part),'wb') as out:
            out.setparams((1,2,RATE,0,'NONE','not compressed'));left=round(total*RATE)
            while left:
                count=min(left,RATE*30);out.writeframes(b'\x00\x00'*count);left-=count
    else:
        track=next(a for a in original['audios'] if a['index']==source['audio_stream_index'])
        offset=track['start_seconds']-original['start_seconds']
        run([config['ffmpeg'],'-nostdin','-y','-v','error','-i',original['path'],'-map',f"0:{track['index']}",
             '-vn','-af',f'asetpts=PTS-STARTPTS+({offset})/TB,aresample={RATE}:async=1:first_pts=0,apad',
             '-t',str(total),'-ar',str(RATE),'-ac','1','-c:a','pcm_s16le',part])
    os.replace(part,path);write_json(stamp,{'recipe':recipe,'fingerprint':quick_fingerprint(path)})
    return path


def pcm_slice(path,start,end,out):
    with wave.open(str(path),'rb') as src:
        require(src.getnchannels()==1 and src.getsampwidth()==2 and src.getframerate()==RATE,'分析音频格式不符')
        a=max(0,min(src.getnframes(),round(start*RATE)));b=max(a,min(src.getnframes(),round(end*RATE)))
        src.setpos(a)
        with wave.open(str(out),'wb') as dest:
            dest.setparams((1,2,RATE,0,'NONE','not compressed'));remaining=b-a
            while remaining:
                n=min(remaining,RATE*30);dest.writeframes(src.readframes(n));remaining-=n


def acoustic_map(path):
    # The managed runtime is Python 3.12. audioop reads PCM in C; no video reread.
    import audioop
    levels=[];zeros=[];block=RATE//100;zero_start=None
    with wave.open(str(path),'rb') as src:
        require(src.getnchannels()==1 and src.getsampwidth()==2 and src.getframerate()==RATE,'需要 16kHz 单声道 PCM')
        total=src.getnframes()/RATE;position=0
        while True:
            raw=src.readframes(block)
            if not raw:break
            count=len(raw)//2;rms=audioop.rms(raw,2);peak=audioop.max(raw,2)
            levels.append(20*math.log10(rms/32768) if rms else -100.0)
            if peak==0 and zero_start is None:zero_start=position/RATE
            if peak!=0 and zero_start is not None:
                if position/RATE-zero_start>=.2:zeros.append([zero_start,position/RATE])
                zero_start=None
            position+=count
        if zero_start is not None and total-zero_start>=.2:zeros.append([zero_start,total])
    require(levels,'音频没有采样')
    ordered=sorted(levels);floor=ordered[len(ordered)//10];loud=ordered[min(len(ordered)-1,len(ordered)*9//10)]
    threshold=round(max(-65,min(-32,floor+.35*(loud-floor))),1)
    reliable=loud-floor>=10 and loud>-45
    silence=[];start=None
    for i,db in enumerate(levels+[0]):
        if db<=threshold and start is None:start=i*.01
        if db>threshold and start is not None:
            end=min(total,i*.01)
            if end-start>=.2:silence.append([round(start,4),round(end,4)])
            start=None
    return {'version':4,'duration_seconds':total,'threshold_db':threshold,'floor_db':round(floor,2),
            'loud_db':round(loud,2),'auto_trim_reliable':reliable,'rms_db_10ms':levels,'silences':silence,'digital_silences':zeros,
            'limit':'低能量不等于没有说话；背景声、轻声和情绪停顿仍须人工判断。'}


def cached_map(audio):
    path=Path(audio).with_name('acoustic.json');stamp=quick_fingerprint(audio)
    if path.exists():
        doc=read_json(path)
        if doc.get('audio_fingerprint')==stamp and doc.get('version')==4:return doc
    doc=acoustic_map(audio);doc['audio_fingerprint']=stamp;write_json(path,doc);return doc


def overlap(a,b,intervals):return sum(max(0,min(b,y)-max(a,x)) for x,y in intervals)


def unit_flags(unit,amap,next_unit=None):
    flags=[];a,b=unit['start'],unit['end']
    for x,y in amap.get('silences',[]):
        if min(b,y)-max(a,x)>=.6:flags.append({'kind':'pause','start':max(a,x),'end':min(b,y),'text':'⏸ 低能量停顿；是否压缩要结合语境'})
    if overlap(a,b,amap.get('silences',[]))>=.9*(b-a):
        flags.append({'kind':'suspected_asr','start':a,'end':b,'text':'❓ 文字主要落在低能量区，疑似误识别；不能据此直接删除'})
    supplied=unit.get('words') or []
    words=[w for w in supplied if isinstance(w,dict) and isinstance(w.get('start'),(int,float)) and isinstance(w.get('end'),(int,float))
           and math.isfinite(w['start']) and math.isfinite(w['end']) and w['start']<w['end'] and isinstance(w.get('word',w.get('text','')),str)] if isinstance(supplied,list) else []
    for w in words:
        value=w.get('word',w.get('text','')).strip(' ，。！？,.!?')
        if value in FILLERS:flags.append({'kind':'filler','start':w['start'],'end':w['end'],'text':f'✂ 语气词候选「{value}」，先判断是否承担回应或情绪'})
    for p,q in zip(words,words[1:]):
        first=p.get('word',p.get('text','')).strip();second=q.get('word',q.get('text','')).strip()
        if first and first==second:flags.append({'kind':'repeat','start':p['start'],'end':p['end'],'text':f'↻ 重复词「{first}」；可能是强调，不自动删除'})
    spans=[(w['start']-.08,w['end']+.08) for w in words if isinstance(w.get('start'),(int,float)) and isinstance(w.get('end'),(int,float))]
    covered=sorted(spans+[(x,y) for x,y in amap.get('silences',[]) if y>a and x<b]);cursor=a
    if words:
        for x,y in covered+[(b,b)]:
            if min(x,b)-cursor>=.35:flags.append({'kind':'unrecognized_sound','start':cursor,'end':min(x,b),'text':'❓ 有声但没有识别出文字；可能是呼吸、笑声或漏字，不自动当卡壳删除'})
            cursor=max(cursor,min(b,y))
    if next_unit:
        p=re.sub(r'\W','',unit['text']);q=re.sub(r'\W','',next_unit['text'])
        if len(p)>=4 and (p==q or p[:4]==q[:4]):flags.append({'kind':'restart','text':'↻ 与下一句开头相似，检查是否重来或承接'})
    from cy_quality import dense_habits
    for d in dense_habits(words):flags.append({'kind':'dense_habits','start':d['start'],'end':d['end'],'text':f"✂ 10 秒内有 {d['count']} 次疑似口癖；正常指代与数量词保留，密集处回听判断"})
    return flags


def subtract_ranges(start,end,cuts):
    parts=[(start,end)]
    for x,y in sorted(cuts):
        fresh=[]
        for a,b in parts:
            if y<=a or x>=b:fresh.append((a,b));continue
            if a<x:fresh.append((a,min(x,b)))
            if y<b:fresh.append((max(y,a),b))
        parts=fresh
    return [(a,b) for a,b in parts if b-a>.0001]


def pause_cuts(start,end,amap,words,policy,protected=()):
    if not policy.get('compress',True) or not amap.get('auto_trim_reliable'):return []
    threshold=float(policy.get('threshold_seconds',.6));head=float(policy.get('head_seconds',.15));tail=float(policy.get('tail_seconds',.2))
    require(.3<=threshold<=10 and 0<=head<=2 and 0<=tail<=2 and head+tail<=threshold,'停顿设置不合理')
    result=[]
    for x,y in amap.get('silences',[]):
        x=max(start,x);y=min(end,y)
        if y-x<=threshold:continue
        a=x+tail;b=y-head
        if x<=start+.025:a=start
        if y>=end-.025:b=end
        if b<=a:continue
        if any(max(a,p)<min(b,q) for p,q in protected):continue
        # A timed word near a proposed deletion protects low-volume phonemes.
        if any(w.get('start',0)-.06<b and w.get('end',0)+.06>a for w in words):continue
        result.append({'start':a,'end':b,'reason':'实测低能量停顿压缩','type':'pause'})
    return result


def build_preview(root,project,output,config,path):
    wav=Path(path);wav.parent.mkdir(parents=True,exist_ok=True);fps=float(project['timeline']['fps'].split('/')[0])/float(project['timeline']['fps'].split('/')[1]) if '/' in project['timeline']['fps'] else float(project['timeline']['fps'])
    cache={};warnings=[];written=0
    with wave.open(str(wav),'wb') as dst:
        dst.setparams((1,2,RATE,0,'NONE','not compressed'))
        for s in output['segments']:
            sid=s['source_id']
            if sid not in cache:cache[sid]=ensure_audio(root,project['sources'][sid],config)
            target=round((s['timeline_in_frame']+s['duration_frames'])/fps*RATE)-written
            with wave.open(str(cache[sid]),'rb') as src:
                start=round(s['in_seconds']*RATE);src.setpos(min(src.getnframes(),start));left=target
                while left>0:
                    n=min(left,RATE*30);raw=src.readframes(n)
                    if len(raw)<n*2:
                        raw+=b'\x00'*(n*2-len(raw));warnings.append(f"{s['id']} 音频不足，试听尾部补静音，需检查同步")
                    dst.writeframes(raw);left-=n;written+=n
    return warnings


def recheck(preview,output,fps):
    amap=acoustic_map(preview);fps=float(fps);issues=[]
    def where(t):
        clip=next((s for s in output['segments'] if s['timeline_in_frame']/fps<=t<(s['timeline_in_frame']+s['duration_frames'])/fps),output['segments'][-1])
        return {'timeline_seconds':round(t,4),'source_id':clip['source_id'],'source_seconds':round(clip['in_seconds']+t-clip['timeline_in_frame']/fps,4)}
    for a,b in amap['silences']:
        if b-a>.6:issues.append(dict(where(a),kind='remaining_pause',duration_seconds=round(b-a,4)))
    speech=[];cur=0
    for a,b in amap['silences']+[[amap['duration_seconds'],amap['duration_seconds']]]:
        if a>cur:speech.append((cur,a))
        cur=max(cur,b)
    for n,(a,b) in enumerate(speech):
        before=a-(speech[n-1][1] if n else 0);after=(speech[n+1][0] if n+1<len(speech) else amap['duration_seconds'])-b
        if .08<=b-a<=.45 and before>=.3 and after>=.3:issues.append(dict(where(a),kind='isolated_sound',note='短促发声可能是词语、笑声或卡壳，待判断'))
    for cue in output['captions']:
        text=re.sub(r'\s','',cue['text'])
        if re.search(r'(.{2,4})\1',text):issues.append(dict(where(cue['in_frame']/fps),kind='repeated_caption_text',text=cue['text']))
    return {'status':'measured_not_human_reviewed','issues':issues,'acoustic':amap,
            'limit':'只测声音能量和文本模式，不能证明无卡壳、语义正确或编辑器同步。'}
