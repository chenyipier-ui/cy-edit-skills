"""Resumable local ASR, progressive reading, measured silence flags and reversible seam reconciliation."""
from __future__ import annotations
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from lve_common import atomic_text,digest,now,project_at,read_json,require,write_json
from lve_sources import check_sources
from lve_project import store_transcript
from lve_audio import cached_map,ensure_audio,overlap,pcm_slice,unit_flags

PROMPT_ZH='嗯，那个，我我我们今天就是，就是说一下这个，呃，这个事情。普通话口语，照实记录语气词和重复，使用简体中文。'


def reconcile(units):
    """Only collapse overlapping duplicate recognitions; other overlaps become one review unit without losing audio/text."""
    result=[];changes=[]
    for item in sorted(units,key=lambda u:(u['start'],u['end'])):
        u=dict(item)
        if result and result[-1]['end']>u['start']:
            old=result[-1];p=re.sub(r'\s','',old['text']);q=re.sub(r'\s','',u['text'])
            intersection=min(old['end'],u['end'])-u['start']
            if p==q and intersection>=.5*min(old['end']-old['start'],u['end']-u['start']) and old.get('chunk')!=u.get('chunk'):
                chosen=old if len(old.get('words',[]))>=len(u.get('words',[])) else u
                chosen=dict(chosen,start=min(old['start'],u['start']),end=max(old['end'],u['end']))
                chosen['seam_note']='重叠分块的相同识别已去重；原始分块保留';result[-1]=chosen
                changes.append({'kind':'duplicate','left':old,'right':u});continue
            merged=dict(old,end=max(old['end'],u['end']),text=old['text']+' '+u['text'],words=[],
                        seam_note='重叠的不同识别合成一个审阅单元，声音范围完整保留；文字重复或多人抢话待核对')
            merged['merged_from']=old.get('merged_from',[old])+[u];result[-1]=merged
            changes.append({'kind':'merged_overlap','start':merged['start'],'end':merged['end']});continue
        result.append(u)
    return result,changes


def progressive(root,sid,index,collected,amap,cache,total_chunks):
    from lve_assist import write_packets
    folder=Path(root)/'reading-in-progress'/sid/cache.name
    provisional=[dict(u,id=f'{sid}_p{n:06d}') for n,u in enumerate(collected,1)]
    write_packets(folder,provisional,{sid:{'label':sid,'original':{'path':'见素材索引'}}},8,{sid:amap},partial=True)
    report={'source_id':sid,'completed_chunks':index+1,'total_chunks':total_chunks,'reading':str(folder/'index.md'),
            'state':'partial_do_not_approve','updated_at':now()}
    write_json(Path(root)/'prepare-progress.json',report)
    atomic_text(Path(root)/'正在生成的阅读稿.md',f"# 正在生成的阅读稿\n\n已识别 {sid} 第 {index+1}/{total_chunks} 段。\n\n[现在可以先读]({folder.relative_to(root).as_posix()}/index.md)\n\n这些编号是临时编号，不写入最终方案。全片结束后以分份阅读索引为准。\n")
    print(f"正在识别 {sid} 第 {index+1}/{total_chunks} 段；可先读 {folder/'index.md'}",file=sys.stderr,flush=True)


def faster_engine(model,device,offline):
    from faster_whisper import WhisperModel
    import ctranslate2
    note=None
    if device=='auto':
        try:chosen='cuda' if ctranslate2.get_cuda_device_count()>0 else 'cpu'
        except Exception:chosen='cpu'
    else:chosen=device
    try:engine=WhisperModel(str(model),device=chosen,compute_type='float16' if chosen=='cuda' else 'int8',local_files_only=offline)
    except Exception as exc:
        if device!='auto' or chosen!='cuda':raise
        note=f'GPU 未能加载，已回退 CPU：{str(exc)[:250]}'
        chosen='cpu';engine=WhisperModel(str(model),device='cpu',compute_type='int8',local_files_only=offline)
    return engine,chosen,note


def transcribe(directory,config,model=None,language=None,chunk_seconds=480,offline=False,source_ids=None,device=None):
    root,project=project_at(directory);sources=check_sources(project);started=time.monotonic()
    backend=config.get('backend');require(backend in ('mlx','faster'),'需要本地识别后端，或通过 prepare --transcripts 导入已有 SRT')
    require(30<=chunk_seconds<=1800,'分块使用 30–1800 秒')
    chosen=source_ids or list(sources);require(len(chosen)==len(set(chosen)) and set(chosen)<=set(sources),'来源编号重复或不存在')
    language=None if language in ('auto','') else language
    model=model or config.get('model_path') or config.get('model');require(model,'没有模型配置')
    if offline:os.environ['HF_HUB_OFFLINE']='1'
    old=read_json(root/'transcript.json')['units'] if (root/'transcript.json').exists() else []
    combined=[u for u in old if u['source_id'] not in chosen];engine=None;report=[];warnings=[];actual_device='mlx' if backend=='mlx' else None
    device=device or config.get('device','auto');prompt=PROMPT_ZH if language and language.startswith('zh') else None
    for sid in chosen:
        source=sources[sid];media=source['original'];audio=ensure_audio(root,source,config)
        if source['audio_stream_index'] is None:
            report.append({'source_id':sid,'status':'no_audio','units':0});continue
        options={'backend':backend,'model':str(model),'revision':config.get('model_revision'),'language':language,
                 'chunk_seconds':chunk_seconds,'overlap_seconds':1,'source':media['identity'],'audio_stream':source['audio_stream_index'],
                 'prompt':prompt,'device_request':device,'version':3}
        cache=root/'analysis'/sid/digest(options)[:20];cache.mkdir(parents=True,exist_ok=True)
        total=media['duration_seconds'];count=math.ceil(total/chunk_seconds);collected=[];reused=0
        with ThreadPoolExecutor(max_workers=1) as pool:
            acoustic_future=pool.submit(cached_map,audio)
            for index in range(count):
                a=index*chunk_seconds;b=min(total,(index+1)*chunk_seconds);start=max(0,a-1);end=min(total,b+1)
                record=cache/f'{index:05d}.json';amap=acoustic_future.result() if acoustic_future.done() else None
                if record.exists():
                    cached=read_json(record);require(cached['options_sha256']==digest(options),'分块缓存版本不符');results=cached['segments'];reused+=1
                elif amap and overlap(start,end,amap['digital_silences'])>=end-start-.001:
                    results=[];write_json(record,{'options_sha256':digest(options),'chunk_start':start,'segments':[],'skipped':'all_samples_zero'})
                else:
                    wav=cache/f'{index:05d}.wav';pcm_slice(audio,start,end,wav)
                    if backend=='mlx':
                        import mlx_whisper
                        results=mlx_whisper.transcribe(str(wav),path_or_hf_repo=str(model),language=language,
                            word_timestamps=True,temperature=0,initial_prompt=prompt,condition_on_previous_text=False,verbose=None)['segments']
                    else:
                        if engine is None:
                            engine,actual_device,note=faster_engine(model,device,offline)
                            if note:warnings.append(note);print(note,file=sys.stderr,flush=True)
                        try:
                            stream,_=engine.transcribe(str(wav),language=language,word_timestamps=True,vad_filter=True,temperature=0,
                                                       initial_prompt=prompt,condition_on_previous_text=False)
                            results=[{'start':s.start,'end':s.end,'text':s.text,'words':[{'start':w.start,'end':w.end,'word':w.word,'probability':w.probability} for w in s.words or []]} for s in stream]
                        except Exception:
                            if device!='auto' or actual_device!='cuda':raise
                            engine,actual_device,_=faster_engine(model,'cpu',offline);warnings.append('GPU 推理失败，本块已改用 CPU；没有把失败标成完成')
                            stream,_=engine.transcribe(str(wav),language=language,word_timestamps=True,vad_filter=True,temperature=0,initial_prompt=prompt,condition_on_previous_text=False)
                            results=[{'start':s.start,'end':s.end,'text':s.text,'words':[{'start':w.start,'end':w.end,'word':w.word} for w in s.words or []]} for s in stream]
                    results=[{'start':float(s['start']),'end':float(s['end']),'text':s['text'],
                              'words':[{k:(str(w[k]) if k=='word' else float(w[k])) for k in ('start','end','word','probability') if k in w} for w in s.get('words',[])]} for s in results]
                    write_json(record,{'options_sha256':digest(options),'chunk_start':start,'segments':results})
                amap=acoustic_future.result()
                for segment in results:
                    x=max(0,float(segment['start'])+start);y=min(total,float(segment['end'])+start)
                    if y<=x or not a<=(x+y)/2<b or not segment['text'].strip():continue
                    if overlap(x,y,amap['digital_silences'])>=y-x-.001:continue
                    unit={'source_id':sid,'start':x,'end':y,'text':segment['text'].strip(),'speaker':'unknown','timing':'asr_unverified','chunk':index,
                          'words':[dict(w,start=float(w['start'])+start,end=float(w['end'])+start) for w in segment.get('words',[]) if float(w['end'])>float(w['start'])]}
                    unit['flags']=unit_flags(unit,amap);collected.append(unit)
                progressive(root,sid,index,collected,amap,cache,count)
        final,changes=reconcile(collected)
        old_by_id={u['id']:u for u in old if u['source_id']==sid}
        for n,u in enumerate(final,1):
            u['id']=f'{sid}_u{n:06d}';u['flags']=unit_flags(u,amap)
            prior=old_by_id.get(u['id'])
            if prior and all(prior.get(k)==u.get(k) for k in ('start','end','text')):u['speaker']=prior.get('speaker','unknown')
        write_json(cache/'seam-changes.json',changes);combined+=final
        info={'source_id':sid,'chunks':count,'reused_chunks':reused,'units':len(final),'status':'asr_unverified' if final else 'no_text_needs_review','acoustic':str(audio.with_name('acoustic.json'))}
        report.append(info)
    # Reload state so concurrent read-only work cannot lose preparation metadata.
    _,project=project_at(root)
    for item in report:project.setdefault('prepared_sources',{})[item['source_id']]=dict(item,source_identity=sources[item['source_id']]['original']['identity'])
    project['preparation_complete']=set(project.get('prepared_sources',{}))==set(project['sources'])
    write_json(root/'project.json',project)
    result=store_transcript(root,project,combined,{'type':'local_asr','backend':backend,'model':str(model),'sources':report,'warnings':warnings})
    elapsed=round(time.monotonic()-started,2)
    write_json(root/'prepare-progress.json',{'state':'complete' if project['preparation_complete'] else 'partial','elapsed_seconds':elapsed,'reading':str(root/'分份阅读索引.md')})
    result.update(sources=report,warnings=warnings,backend=backend,device=actual_device,model=str(model),elapsed_seconds=elapsed)
    return result
