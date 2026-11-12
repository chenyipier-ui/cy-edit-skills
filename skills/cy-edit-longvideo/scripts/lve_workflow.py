"""Five ordinary commands compose the lower-level operations without hiding failure states."""
from __future__ import annotations
import time
from pathlib import Path
from lve_common import atomic_text,project_at,read_json,require,write_json
from lve_project import init_project,import_transcript
from lve_audio import ensure_audio,cached_map


def prepare(args,config,data):
    started=time.monotonic();brief=read_json(args.brief) if args.brief else None
    init_project(args.project,args.media,args.name,config,args.input_list,args.fps,'reference',brief)
    root,project=project_at(args.project)
    if brief is not None and brief!=project.get('brief'):
        project['brief']=brief;project['approval']=None;write_json(root/'project.json',project)
    from lve_feedback_flow import experience
    experience_result=experience(root,data,project.get('brief',{}).get('purpose') or project['name'])
    if args.transcripts:
        source_list=Path(args.transcripts).expanduser().resolve();mapping=read_json(source_list).get('sources',{})
        require(isinstance(mapping,dict) and set(mapping)<=set(project['sources']),'时间稿清单使用 sources: {真实来源编号: SRT 路径}')
        for sid,filename in mapping.items():
            path=(source_list.parent/Path(filename).expanduser()).resolve();import_transcript(root,path,sid)
        _,project=project_at(root)
        for sid,source in project['sources'].items():
            cached_map(ensure_audio(root,source,config))
            if sid in mapping or source['audio_stream_index'] is None:
                project.setdefault('prepared_sources',{})[sid]={'source_identity':source['original']['identity'],'status':'imported' if sid in mapping else 'no_audio'}
        project['preparation_complete']=set(project.get('prepared_sources',{}))==set(project['sources']);write_json(root/'project.json',project)
        if (root/'transcript.json').exists():
            from lve_assist import packets
            packets(root)
        result={'preparation_complete':project['preparation_complete'],'reading':str(root/'分份阅读索引.md')}
    elif args.backend=='none' or args.no_asr:
        for source in project['sources'].values():cached_map(ensure_audio(root,source,config))
        result={'preparation_complete':False,'next':'在剪映识别原片字幕并导出 SRT；按 examples/transcripts.json 记录对应文件，再用相同 prepare 命令加 --transcripts。'}
    else:
        from lve_transcribe import transcribe
        result=transcribe(root,config,language=args.language,chunk_seconds=args.chunk_seconds,offline=args.offline,device=args.device)
    result['elapsed_seconds_in_prepare']=round(time.monotonic()-started,2)
    result['experience']=experience_result
    atomic_text(root/'准备状态.md','# 准备状态\n\n'+str(result.get('next') or '查看分份阅读索引；先读完整片，再提出方案。')+'\n\n准备耗时为本次实际运行记录，不包含首次环境和模型下载。\n')
    return result


def build(args,config):
    from lve_assist import build_plan
    from lve_media import export_project
    root,p=project_at(args.project);require(not (root/'deliveries'/args.version).exists(),'该交付版本已存在，使用新版本号')
    if args.plan:
        require(not args.corrections,'使用已核对计划时不另改字幕；先生成新计划再记录核对');plan=Path(args.plan)
    else:
        plan=root/'plans'/(args.version+'.json')
        if not plan.exists():build_plan(root,plan,corrections_file=args.corrections)
        else:require(not args.corrections,'本版本计划已存在；有新的改字请使用新版本号')
    return export_project(root,plan,args.version,config,skip_recheck=getattr(args,'skip_recheck',False),skip_reason=getattr(args,'recheck_skipped',None))
