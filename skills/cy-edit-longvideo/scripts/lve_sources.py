"""Reference original media by elapsed time. Routine identity checks read bounded samples."""
from __future__ import annotations
import hashlib
import math
import os
import platform
import shutil
from fractions import Fraction
from pathlib import Path
from lve_common import digest, identifier, now, read_json, require, run, sha_file, write_json

EXTENSIONS = {'.mp4','.mov','.m4v','.mkv','.avi','.mts','.m2ts','.webm'}
SAMPLE_BYTES = 4 * 1024 * 1024


def quick_fingerprint(path):
    path=Path(path); before=path.stat()
    with path.open('rb') as f:
        head=f.read(SAMPLE_BYTES)
        f.seek(max(0,before.st_size-SAMPLE_BYTES)); tail=f.read(SAMPLE_BYTES)
    after=path.stat()
    require((before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),'媒体仍在写入，等待拷贝完成再登记')
    return {'size':after.st_size,'mtime_ns':after.st_mtime_ns,'sample_bytes':SAMPLE_BYTES,
            'head_sha256':hashlib.sha256(head).hexdigest(),'tail_sha256':hashlib.sha256(tail).hexdigest()}


def content_sample(fp):
    return {k:fp[k] for k in ('size','sample_bytes','head_sha256','tail_sha256')}


def is_cover_art(stream):
    """MP4/M4V embedded cover pictures are video streams with disposition attached_pic; they are not footage."""
    try:
        from av.stream import Disposition
        return bool(stream.disposition & Disposition.attached_pic)
    except Exception:
        return False


def inspect_media(path):
    import av
    path=Path(path).expanduser().resolve();require(path.is_file(),f'媒体不存在：{path}')
    with av.open(str(path)) as c:
        videos=[s for s in c.streams.video if not is_cover_art(s)]
        require(videos,'没有可用的视频画面（只有封面图）')
        require(len(videos)==1,'多视频流需先明确所用画面；多机位请分别提供文件')
        v=videos[0]
        average=v.average_rate
        nominal=v.base_rate or getattr(v,'guessed_rate',None) or average
        require(nominal and nominal>0,'没有可用名义帧率；请明确素材时间基准')
        origin=float(Fraction(v.start_time or 0)*v.time_base)
        duration=float(Fraction(v.duration)*v.time_base) if v.duration is not None else float((c.duration or 0)/av.time_base)
        require(duration>0,'无法取得时长')
        audios=[{'index':a.index,'channels':a.codec_context.channels,'sample_rate':a.codec_context.sample_rate,
                 'start_seconds':float(Fraction(a.start_time or 0)*a.time_base)} for a in c.streams.audio]
        transfer=getattr(v.codec_context,'color_trc',None)
        rotation=v.metadata.get('rotate')
        orientation_status='metadata_only'
        try:
            first=next(c.decode(v),None)
            if first is not None:
                rotation=str(getattr(first,'rotation',rotation or 0));orientation_status='first_frame_metadata'
                if first.pts is not None:
                    first_time=float(Fraction(first.pts)*first.time_base)
                    duration=max(.001,duration+origin-first_time);origin=first_time
        except Exception:
            orientation_status='first_frame_unreadable_check_import'
        media={'path':str(path),'name':path.name,'fps':str(Fraction(nominal)),
               'average_fps':str(Fraction(average)) if average else None,'frames_reported':v.frames,
               'width':v.codec_context.width,'height':v.codec_context.height,'duration_seconds':duration,
               'start_seconds':origin,'video_stream_index':v.index,'audios':audios,
               'video_codec':v.codec_context.name,'pixel_format':v.codec_context.format.name if v.codec_context.format else '',
               'hdr':transfer in (16,18) or any(x in str(transfer).lower() for x in ('2084','arib','hlg','pq')),
               'rotation_tag':rotation,'orientation_status':orientation_status,
               'timestamp_status':'metadata_only_not_full_pts_scan'}
    media['fingerprint']=quick_fingerprint(path)
    media['identity']=digest(content_sample(media['fingerprint']))
    media['size_bytes']=media['fingerprint']['size']
    media['rate_difference']=abs(float(Fraction(media['average_fps'])/Fraction(media['fps']))-1) if media['average_fps'] else None
    return media


def collect_inputs(paths=None,input_list=None):
    require(not (paths and input_list),'--media 与 --input-list 选一种')
    records=[];base=Path.cwd()
    if input_list:
        doc=read_json(input_list);require(doc.get('schema_version')==1,'素材清单版本须为 1')
        records=doc.get('sources',[]);base=Path(input_list).expanduser().resolve().parent
    else:
        for raw in paths or []:
            p=Path(raw).expanduser().resolve()
            records += [{'path':str(x)} for x in sorted(p.iterdir()) if x.is_file() and x.suffix.lower() in EXTENSIONS] if p.is_dir() else [{'path':str(p)}]
    require(isinstance(records,list) and records,'没有素材；提供文件、文件夹或素材清单')
    result=[];ids=set();seen=set()
    for i,item in enumerate(records,1):
        p=(base/Path(item['path']).expanduser()).resolve();require(p.is_file(),f'文件不存在：{p}')
        if str(p) in seen:continue
        sid=identifier(item.get('id',f's{i:04d}'));require(sid.casefold() not in ids,'素材编号重复')
        ids.add(sid.casefold());seen.add(str(p));result.append(dict(item,id=sid,path=str(p)))
    return result


def sources_digest(sources):
    return digest([{'id':sid,'original_identity':s['original']['identity'],'working_identity':s['working']['identity'],
                    'original_clock':{k:s['original'][k] for k in ('fps','start_seconds','duration_seconds','width','height')},
                    'working_clock':{k:s['working'][k] for k in ('fps','start_seconds','duration_seconds','width','height')},
                    'mapping':s['mapping'],'audio_stream_index':s.get('audio_stream_index')} for sid,s in sorted(sources.items())])


def media_warnings(media,target=None):
    notes=[]
    if media.get('rate_difference') is not None and media['rate_difference']>.01:
        notes.append('平均与名义帧率相差超过 1%；仅是风险提示，不足以判定 VFR 严重或必须转码')
    if target and Fraction(media['fps'])!=target:notes.append('混合帧率：按秒定位，需在目标编辑器检查同步和总时长')
    if media.get('hdr'):notes.append('HDR 原片直接引用；导入后检查色彩，不自动转 SDR')
    if media.get('rotation_tag') not in (None,'0','0.0',''):notes.append('原片带旋转信息，导入后检查方向')
    return notes


def inspect_inputs(paths,input_list=None,fps=None,policy='reference',out=None):
    records=collect_inputs(paths,input_list);media=[inspect_media(i['path']) for i in records]
    target=Fraction(str(fps or media[0]['fps']));require(0<target<=240,'项目帧率不合理')
    report={'target_fps':str(target),'sources':[],
            'note':'默认引用原片；读取文件头与首尾各 4 MiB，不复制或转码视频。抽样不能发现全部中段变更。'}
    for i,m in zip(records,media):
        report['sources'].append({'source_id':i['id'],'name':m['name'],'duration_seconds':m['duration_seconds'],
            'action':'choose_audio_track' if len(m['audios'])>1 and i.get('audio_stream_index') is None else 'reference_original',
            'audio_tracks':m['audios'],'estimated_analysis_audio_bytes':math.ceil(m['duration_seconds']*32000),
            'warnings':media_warnings(m,target),'elapsed_time_estimate':None})
    if out:write_json(out,report)
    return report


def prepare_sources(root,records,config,fps=None,policy='reference'):
    require(policy in ('reference','auto','preserve'),'prepare 默认引用原片；单条转码使用 conform 并保存用户授权')
    sources={};target=None
    for item in records:
        m=inspect_media(item['path']);target=target or Fraction(str(fps or m['fps']))
        require(0<target<=240,'项目帧率不合理')
        chosen=item.get('audio_stream_index')
        if len(m['audios'])>1:require(type(chosen) is int,f"{m['name']} 有多音轨，请在素材清单填 audio_stream_index；可选 {[a['index'] for a in m['audios']]}")
        chosen=chosen if chosen is not None else (m['audios'][0]['index'] if m['audios'] else None)
        require(chosen is None or chosen in {a['index'] for a in m['audios']},'选中音轨不存在')
        sid=item['id'];sources[sid]={'id':sid,'label':item.get('label',m['name']),'group':item.get('group','unspecified'),
            'role':item.get('role','speech'),'original':m,'working':dict(m),'audio_stream_index':chosen,
            'mapping':{'type':'reference_original','clock':'elapsed_seconds_from_video_start'},'warnings':media_warnings(m,target)}
    first=next(iter(sources.values()))['working']
    # Orientation is checked at import. Do not silently bake rotation or guess display-matrix metadata.
    rotation=float(first.get('rotation_tag') or 0)
    width,height=first['width'],first['height']
    if abs(rotation)%180==90:width,height=height,width
    return sources,{'fps':str(target),'width':width,'height':height,'normalization':'reference','clock':'elapsed_seconds'}


def ensure_v2(project):
    require(project.get('schema_version')==3 and project.get('sources'),'此入口处理新项目；旧工程请保留，用新目录 prepare，不覆盖旧反馈')


def check_sources(project,full_hash=False):
    ensure_v2(project);require(sources_digest(project['sources'])==project['sources_sha256'],'来源登记被改动，请恢复登记或使用 relink')
    for sid,s in project['sources'].items():
        m=s['working'];p=Path(m['path']);require(p.is_file(),f'{sid} 原片离线，使用 relink')
        fp=quick_fingerprint(p)
        require(fp==m['fingerprint'],f'{sid} 文件大小、时间或抽样改变；不要静默沿用旧缓存，先核对文件')
        if full_hash:
            require(m.get('sha256'),f'{sid} 没有完整哈希基线；先运行 verify-sources --full 建立本次基线')
            require(sha_file(p)==m['sha256'],f'{sid} 完整哈希不符')
    return project['sources']


def verify_sources(directory,full=False):
    from lve_common import project_at
    root,project=project_at(directory);check_sources(project)
    records=[]
    for sid,s in project['sources'].items():
        m=s['working'];row={'source_id':sid,'quick_check':'matched'}
        if full:
            value=sha_file(m['path']);old=m.get('sha256')
            require(not old or value==old,f'{sid} 完整哈希改变')
            m['sha256']=value
            if s['original']['identity']==m['identity']:s['original']['sha256']=value
            row.update(full_check='matched_existing_baseline' if old else 'baseline_created_now',sha256=value)
        records.append(row)
    write_json(root/'project.json',project)
    report={'recorded_at':now(),'sources':records,'limit':'首次建立哈希只能固定现在的内容，不能证明此前没有变化。'}
    write_json(root/'source-checks'/(digest(report)[:20]+'.json'),report);return report


def conform(directory,source_id,config,reply_file,fps=None,max_height=1080,encoder='auto'):
    from lve_common import project_at
    root,project=project_at(directory);check_sources(project)
    require(source_id in project['sources'],'素材编号不存在')
    evidence=Path(reply_file).read_text(encoding='utf-8').strip();require(evidence,'单条派生副本需要用户确认的用途和成本记录')
    s=project['sources'][source_id];original=s['original'];require(not original['hdr'],'HDR 转换须先确定色彩方案；本命令不默认转 SDR')
    target=Fraction(str(fps or project['timeline']['fps']));require(0<target<=240 and 240<=max_height<=2160,'帧率或高度无效')
    ffmpeg=config['ffmpeg'];available=run([ffmpeg,'-hide_banner','-encoders'])
    if encoder=='auto':encoder='h264_videotoolbox' if platform.system()=='Darwin' else 'h264_nvenc'
    require(encoder in ('h264_videotoolbox','h264_nvenc','libx264') and encoder in available,
            '当前 FFmpeg 没有所选硬件编码器；继续链接原片，或说明 CPU 成本后显式选择 libx264')
    folder=root/'media'/source_id;folder.mkdir(parents=True,exist_ok=True)
    token=digest([s['original']['identity'],str(target),max_height,encoder])[:16]
    output=folder/(token+'.mp4');require(not output.exists(),'这个派生版本已存在，请先查看记录，不覆盖')
    require(shutil.disk_usage(folder).free>max(1024**3,original['size_bytes']),'派生媒体盘空间不足；估计并非容量保证')
    part=folder/(token+'.partial.mp4')
    cmd=[ffmpeg,'-nostdin','-y','-v','error','-i',original['path'],'-map',f"0:{original['video_stream_index']}"]
    if s['audio_stream_index'] is not None:cmd+=['-map',f"0:{s['audio_stream_index']}"]
    cmd+=['-vf',f"setpts=PTS-STARTPTS,fps={target},scale=-2:'min(ih,{max_height})'",'-c:v',encoder,'-pix_fmt','yuv420p']
    cmd+=['-preset','fast','-crf','18'] if encoder=='libx264' else ['-b:v','12M']
    if s['audio_stream_index'] is not None:
        track=next(a for a in original['audios'] if a['index']==s['audio_stream_index'])
        offset=track['start_seconds']-original['start_seconds']
        cmd+=['-af',f'asetpts=PTS-STARTPTS+({offset})/TB,aresample=48000:async=1:first_pts=0','-c:a','aac']
    else:cmd+=['-an']
    cmd+=['-map_metadata','-1','-metadata:s:v:0','rotate=0','-movflags','+faststart',part];run(cmd)
    os.replace(part,output);m=inspect_media(output)
    require(abs(m['duration_seconds']-original['duration_seconds'])<.15,'派生媒体时长变化过大；保留原片链接并人工检查')
    s['working']=m;s['mapping']={'type':'explicit_conform','clock':'elapsed_seconds_from_video_start','evidence':evidence,'encoder':encoder}
    project['sources_sha256']=sources_digest(project['sources']);project['approval']=None
    write_json(root/'project.json',project)
    if (root/'transcript.json').exists():
        from lve_project import store_transcript
        t=read_json(root/'transcript.json');store_transcript(root,project,t['units'],dict(t['provenance'],media_rebound=True))
    write_json(folder/(token+'.json'),{'evidence':evidence,'source':original['identity'],'working':m['identity'],'created_at':now()})
    return {'message':'单条派生副本已生成，原片保留。内容时间稿保留；请重提方案以绑定新媒体。','path':str(output)}
