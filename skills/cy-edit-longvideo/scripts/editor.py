#!/usr/bin/env python3
"""cy-edit-longvideo: prepare → propose → approve → build → feedback."""
from __future__ import annotations
import argparse
import contextlib
import importlib.util
import json
import os
import platform
import sys
from pathlib import Path
from lve_common import VERSION,digest,lock,now,project_at,read_json,require,state_dir,write_json


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir');p.add_argument('--config');p.add_argument('--backend',choices=['auto','faster','mlx','none'])
    p.add_argument('--offline',action='store_true');p.add_argument('--no-asr',action='store_true',help=argparse.SUPPRESS)
    sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('prepare');q.add_argument('--project',required=True);q.add_argument('--name',required=True);q.add_argument('--media',action='append');q.add_argument('--input-list');q.add_argument('--fps');q.add_argument('--brief');q.add_argument('--transcripts');q.add_argument('--model');q.add_argument('--language',default='zh');q.add_argument('--chunk-seconds',type=int,default=480);q.add_argument('--device',choices=['auto','cpu','cuda'],default='auto')
    q=sub.add_parser('propose');q.add_argument('--project',required=True);q.add_argument('--input',required=True)
    q=sub.add_parser('approve');q.add_argument('--project',required=True);q.add_argument('--selected',required=True);q.add_argument('--reply-file','--evidence-file',dest='evidence_file',required=True);q.add_argument('--solo-speaker')
    q=sub.add_parser('build');q.add_argument('--project',required=True);q.add_argument('--version',required=True);q.add_argument('--corrections');q.add_argument('--plan');q.add_argument('--model');q.add_argument('--skip-recheck',action='store_true')
    q=sub.add_parser('review');q.add_argument('--records',required=True);q.add_argument('--input',required=True)
    q=sub.add_parser('feedback');q.add_argument('--project',required=True);q.add_argument('--input',required=True)
    q=sub.add_parser('experience');q.add_argument('--project',required=True);q.add_argument('--task');q.add_argument('--decisions-file')
    # Recovery helpers; not extra mandatory workflow stages.
    q=sub.add_parser('inspect-inputs');q.add_argument('--media',action='append');q.add_argument('--input-list');q.add_argument('--fps');q.add_argument('--out')
    q=sub.add_parser('init');q.add_argument('--project',required=True);q.add_argument('--name',required=True);q.add_argument('--media',action='append');q.add_argument('--input-list');q.add_argument('--fps')
    q=sub.add_parser('transcribe');q.add_argument('--project',required=True);q.add_argument('--model');q.add_argument('--language',default='zh');q.add_argument('--chunk-seconds',type=int,default=480);q.add_argument('--offline',action='store_true',default=argparse.SUPPRESS);q.add_argument('--source-id',action='append');q.add_argument('--device',choices=['auto','cpu','cuda'],default='auto')
    q=sub.add_parser('import-transcript');q.add_argument('--project',required=True);q.add_argument('--input',required=True);q.add_argument('--source-id')
    q=sub.add_parser('packets');q.add_argument('--project',required=True);q.add_argument('--minutes',type=int,default=8)
    q=sub.add_parser('templates');q.add_argument('--project',required=True);q.add_argument('--out',required=True)
    q=sub.add_parser('label-speaker');q.add_argument('--project',required=True);q.add_argument('--name',required=True);q.add_argument('--evidence-file',required=True);q.add_argument('--source-id',action='append');q.add_argument('--unit-id',action='append')
    q=sub.add_parser('build-plan');q.add_argument('--project',required=True);q.add_argument('--input');q.add_argument('--corrections');q.add_argument('--out',required=True)
    q=sub.add_parser('review-plan');q.add_argument('--project',required=True);q.add_argument('--plan',required=True);q.add_argument('--segments',default='all');q.add_argument('--checks',required=True);q.add_argument('--evidence-file',required=True);q.add_argument('--out',required=True)
    q=sub.add_parser('validate-plan');q.add_argument('--project',required=True);q.add_argument('--plan',required=True)
    q=sub.add_parser('export');q.add_argument('--project',required=True);q.add_argument('--plan',required=True);q.add_argument('--version',required=True)
    q=sub.add_parser('verify');q.add_argument('--delivery',required=True)
    q=sub.add_parser('verify-sources');q.add_argument('--project',required=True);q.add_argument('--full',action='store_true')
    q=sub.add_parser('relink');q.add_argument('--project',required=True);q.add_argument('--media',required=True);q.add_argument('--source-id',required=True);q.add_argument('--kind',choices=['working','original'],default='working');q.add_argument('--reply-file')
    q=sub.add_parser('conform');q.add_argument('--project',required=True);q.add_argument('--source-id',required=True);q.add_argument('--reply-file',required=True);q.add_argument('--fps');q.add_argument('--max-height',type=int,default=1080);q.add_argument('--encoder',choices=['auto','h264_videotoolbox','h264_nvenc','libx264'],default='auto')
    sub.add_parser('doctor')
    q=sub.add_parser('status');q.add_argument('--project',required=True)
    q=sub.add_parser('record-qa');q.add_argument('--project',required=True);q.add_argument('--input',required=True)
    for name in ('context','adopt-experiences','turn-open','turn-close'):
        q=sub.add_parser(name);q.add_argument('--project',required=True)
        if name in ('adopt-experiences','turn-open'):q.add_argument('--input',required=True)
        if name=='turn-close':q.add_argument('--turn-id',required=True)
    return p


def runtime(args,data,config_file):
    config=read_json(config_file) if config_file.exists() else {}
    if args.command in ('prepare','init','transcribe','import-transcript','inspect-inputs','build','export','conform'):
        from lve_models import backend_name,model_name
        from lve_common import run
        needs_asr=args.command=='transcribe' or (args.command in ('build','export') and not getattr(args,'skip_recheck',False)) or (args.command=='prepare' and not args.transcripts and args.backend!='none' and not args.no_asr)
        require(not (args.command=='transcribe' and (args.backend=='none' or args.no_asr)),'已有时间稿使用 prepare --transcripts 或 import-transcript')
        desired=backend_name(args.backend or (config.get('backend') if config.get('backend') in ('faster','mlx') else 'auto'))
        missing=config.get('schema_version')!=2 or not Path(config.get('python','')).is_file() or not Path(config.get('ffmpeg','')).is_file()
        if needs_asr:
            missing=missing or config.get('backend')=='none' or not Path(config.get('model_path') or '/missing-model-path').is_dir()
            if args.backend is not None:missing=missing or desired!=config.get('backend')
            if getattr(args,'model',None):missing=missing or model_name(desired,args.model)!=config.get('model')
        if missing:
            require(not args.config,'自定义配置不完整，请先通过统一入口配置对应 state-dir')
            entry=Path(__file__).resolve().parent
            command=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(entry/'bootstrap.ps1')] if platform.system()=='Windows' else ['bash',str(entry/'bootstrap.sh')]
            command+=['--state-dir',str(data),'--backend',desired if needs_asr else 'none']
            if args.offline:command+=['--offline']
            if getattr(args,'model',None):command+=['--model',args.model]
            run(command);config=read_json(config_file)
    interpreter=config.get('python')
    if interpreter and Path(interpreter).is_file() and Path(interpreter).absolute()!=Path(sys.executable).absolute() and os.environ.get('CY_EDIT_LONGVIDEO_REEXEC')!=interpreter:
        os.environ['CY_EDIT_LONGVIDEO_REEXEC']=interpreter;os.execv(interpreter,[interpreter,str(Path(__file__).resolve()),*sys.argv[1:]])
    return config


def main():
    args=parser().parse_args();data=state_dir(args.state_dir);config_file=Path(args.config).expanduser().resolve() if args.config else data/'runtime.json'
    try:
        config=runtime(args,data,config_file)
        if args.config and not args.state_dir and config.get('state_dir'):data=Path(config['state_dir'])
        mutations={'prepare','init','propose','approve','build','transcribe','import-transcript','label-speaker','build-plan','review-plan','export','verify-sources','relink','conform','record-qa'}
        project_path=Path(args.project).expanduser().resolve() if hasattr(args,'project') else None
        guard=lock(project_path.parent/('.'+project_path.name+'.cy-edit-lock')) if args.command in mutations else contextlib.nullcontext()
        with guard:
            from lve_project import init_project,import_transcript,propose,approve,relink
            from lve_assist import packets,templates,label_speaker,build_plan,review_plan
            from lve_media import validate_plan,export_project,verify_delivery
            from lve_feedback_flow import feedback,experience
            if args.command=='prepare':
                from lve_workflow import prepare
                result=prepare(args,config,data)
            elif args.command=='build':
                from lve_workflow import build
                result=build(args,config)
            elif args.command=='propose':result=propose(args.project,args.input)
            elif args.command=='approve':result=approve(args.project,args.selected,args.evidence_file,args.solo_speaker)
            elif args.command=='review':
                from cy_quality import record_review
                result=record_review(args.records,args.input)
            elif args.command=='feedback':result=feedback(args.project,args.input,data)
            elif args.command=='experience':result=experience(args.project,data,args.task,args.decisions_file)
            elif args.command=='init':result=init_project(args.project,args.media,args.name,config,args.input_list,args.fps)
            elif args.command=='import-transcript':result=import_transcript(args.project,args.input,args.source_id)
            elif args.command=='transcribe':
                from lve_transcribe import transcribe
                result=transcribe(args.project,config,language=args.language,chunk_seconds=args.chunk_seconds,offline=args.offline,source_ids=args.source_id,device=args.device)
            elif args.command=='packets':result=packets(args.project,args.minutes)
            elif args.command=='templates':result=templates(args.project,args.out,data)
            elif args.command=='label-speaker':result=label_speaker(args.project,args.name,args.evidence_file,args.source_id,args.unit_id)
            elif args.command=='build-plan':result=build_plan(args.project,args.out,args.input,args.corrections)
            elif args.command=='review-plan':result=review_plan(args.project,args.plan,args.out,args.segments,args.checks,args.evidence_file)
            elif args.command=='validate-plan':
                _,_,plan,warnings=validate_plan(args.project,args.plan);result={'structure':'valid','quality':plan['quality'],'warnings':warnings}
            elif args.command=='export':result=export_project(args.project,args.plan,args.version,config)
            elif args.command=='verify':result=verify_delivery(args.delivery)
            elif args.command=='inspect-inputs':
                from lve_sources import inspect_inputs
                result=inspect_inputs(args.media,args.input_list,args.fps,out=args.out)
            elif args.command=='verify-sources':
                from lve_sources import verify_sources
                result=verify_sources(args.project,args.full)
            elif args.command=='conform':
                from lve_sources import conform
                result=conform(args.project,args.source_id,config,args.reply_file,args.fps,args.max_height,args.encoder)
            elif args.command=='relink':result=relink(args.project,args.media,args.source_id,args.kind,args.reply_file)
            elif args.command=='status':_,result=project_at(args.project)
            elif args.command=='doctor':result={'version':VERSION,'python':sys.executable,'configuration':str(config_file),'configured':bool(config),'backend':config.get('backend'),'model':config.get('model'),'note':'配置存在不等于实际识别或编辑器导入通过'}
            elif args.command=='record-qa':
                root,p=project_at(args.project);qa=read_json(args.input);require(p.get('last_delivery') and qa.get('delivery_version')==p['last_delivery']['version'],'需要对应当前交付版本')
                require(qa.get('kind') in ('content','audiovisual','editor_import','user_acceptance') and qa.get('status') in ('passed','failed','partial','not_run') and qa.get('evidence'),'验收需真实状态与依据')
                qa['recorded_at']=now();path=root/'qa'/(digest(qa)[:20]+'.json');write_json(path,qa);result={'qa':str(path)}
            else:
                from lve_feedback import context,adopt,open_turn,close_turn
                if args.command=='context':result=context(args.project,data)
                elif args.command=='adopt-experiences':result=adopt(args.project,args.input,data)
                elif args.command=='turn-open':result=open_turn(args.project,args.input)
                else:result=close_turn(args.project,args.turn_id,data)
            print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
            return 2 if args.command=='turn-close' and not result.get('complete') else 0
    except Exception as exc:
        failure={'ok':False,'error_type':type(exc).__name__,'error':str(exc)}
        if args.command in ('feedback','turn-close'):failure.update(feedback_saved='unconfirmed',instruction='不能报告全部已保存；保留原输入，核对进度文件后重试。')
        print(json.dumps(failure,ensure_ascii=False),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
