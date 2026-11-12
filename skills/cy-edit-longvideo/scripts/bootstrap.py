#!/usr/bin/env python3
"""Complete private runtime and ASR model configuration; invoked by zero-Python wrappers."""
from __future__ import annotations
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from lve_common import UserError, VERSION, atomic_text, lock, now, read_json, require, run, state_dir, write_json
from lve_models import backend_name, model_name

BASE=['imageio-ffmpeg==0.6.0','av==16.0.1']
BACKENDS={'faster':['faster-whisper==1.2.1'],'mlx':['mlx-whisper==0.4.3'],'none':[]}

def execute(args, logfile):
    logfile.parent.mkdir(parents=True,exist_ok=True)
    with logfile.open('a',encoding='utf-8') as log:
        p=subprocess.Popen([str(x) for x in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
        for line in p.stdout:
            print(line,end='',file=sys.stderr,flush=True);log.write(line);log.flush()
        code=p.wait()
    require(code==0,f'配置步骤失败，日志：{logfile}。修复网络／权限后重跑原入口继续。')

def setup(args):
    data=state_dir(args.state_dir);system=platform.system();machine=platform.machine().lower()
    backend=backend_name(args.backend)
    require(system in ('Darwin','Windows'),'本版本自动配置以 macOS 和 Windows 为目标')
    require(system!='Windows' or machine in ('amd64','x86_64'),'Windows 原生 ARM/32 位不属于当前自动配置目标；需要单独适配或经确认的 x64 环境')
    require(backend!='mlx' or (system=='Darwin' and machine in ('arm64','aarch64')),'MLX 只支持 Apple Silicon')
    planned={'system':system,'machine':machine,'backend':backend,'dependencies':BASE+BACKENDS[backend],
             'state_dir':str(data),'python_target':'3.12','network':'官方 uv/Python、PyPI 依赖和公开模型；本地视频不上传'}
    if args.plan:return planned
    data.mkdir(parents=True,exist_ok=True)
    os.environ['PIP_CACHE_DIR']=str(data/'cache/pip')
    status_file=data/'setup-status.json'
    status=dict(planned,stage='starting',started_at=now(),complete=False)
    write_json(status_file,status)
    try:
        require(shutil.disk_usage(data).free>3*1024**3,'运行环境盘至少预留约 3 GiB；模型和长视频工作盘另需空间')
        python=args.python or sys.executable
        version=json.loads(run([python,'-c','import sys,json;print(json.dumps(list(sys.version_info[:2])))']))
        require(tuple(version)==(3,12),'请使用 bootstrap.sh/.ps1 自动获取 Python 3.12；不要求用户手装 Python')
        env=data/'runtime'/('v2-'+backend);runtime=env/('Scripts/python.exe' if system=='Windows' else 'bin/python')
        status['stage']='dependencies';write_json(status_file,status)
        if not runtime.exists():execute([python,'-m','venv',env],data/'logs/setup.log')
        command=[runtime,'-m','pip','install','--disable-pip-version-check','--only-binary=:all:']
        if args.offline:
            command+=['--no-index']
            if args.wheelhouse:command+=['--find-links',str(Path(args.wheelhouse).expanduser().resolve())]
        command+=BASE+BACKENDS[backend]
        execute(command,data/'logs/setup.log')
        modules='import av,imageio_ffmpeg'+(';import ctranslate2,faster_whisper' if backend=='faster' else ';import mlx_whisper' if backend=='mlx' else '')
        try:run([runtime,'-c',modules])
        except UserError:
            if system!='Windows' or args.offline:raise
            status['stage']='windows_visual_cpp_runtime';write_json(status_file,status)
            execute(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',Path(__file__).with_name('install_vcredist.ps1'),
                     '-DownloadDirectory',data/'bootstrap'],data/'logs/setup.log')
            run([runtime,'-c',modules])
        status['stage']='media_tools';write_json(status_file,status)
        ffmpeg=args.ffmpeg or run([runtime,'-c','import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())']).strip()
        ffmpeg=str(Path(ffmpeg).expanduser().resolve());run([ffmpeg,'-version'])
        config=dict(planned,schema_version=2,skill_version=VERSION,python=str(runtime),ffmpeg=ffmpeg,
                    device='auto',compute_type='auto',configured_at=now(),asr_execution='not_run',editor_import='not_run')
        if backend!='none':
            status['stage']='model_download';write_json(status_file,status)
            model=model_name(backend,args.model)
            if Path(model).expanduser().is_dir():model_path=str(Path(model).expanduser().resolve())
            else:
                code='from huggingface_hub import snapshot_download;import sys;print(snapshot_download(repo_id=sys.argv[1],cache_dir=sys.argv[2],local_files_only=sys.argv[3]=="1"))'
                model_path=run([runtime,'-c',code,model,data/'models','1' if args.offline else '0']).strip().splitlines()[-1]
            require(Path(model_path).is_dir(),'模型下载未完成')
            if backend=='faster':require((Path(model_path)/'model.bin').is_file(),'模型缺少 model.bin')
            config.update(model=model,model_path=model_path,model_revision=Path(model_path).name,model_download='complete')
            # Device selection and model loading happen on the first actual transcription.
            status['stage']='model_load';write_json(status_file,status)
            config['model_load']='pending_first_transcribe'
        else:config.update(model=None,model_path=None,model_download='not_required',model_load='not_required')
        config['installed_packages']=run([runtime,'-m','pip','freeze']).splitlines()
        write_json(data/'runtime.json',config)
        atomic_text(data/'python-path.txt',str(runtime)+'\n')
        status.update(stage='configured',complete=True,completed_at=now(),model_load=config['model_load'],asr_execution='not_run')
        write_json(status_file,status)
        atomic_text(data/'首次配置说明.md','# 本机配置记录\n\n'+
                    '\n'.join(f'- {k}：{v}' for k,v in config.items() if k!='installed_packages')+
                    '\n\n已安装与模型可加载不等于识别准确、媒体完整处理或编辑器导入已通过。下一步对用户实际素材逐文件处理。\n')
        return {'config':str(data/'runtime.json'),'complete':True,'backend':backend,'model_load':config['model_load'],'asr_execution':'not_run'}
    except Exception as exc:
        status.update(complete=False,error=str(exc),failed_at=now());write_json(status_file,status)
        raise

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir');p.add_argument('--python');p.add_argument('--backend',choices=['auto','faster','mlx','none'],default='auto')
    p.add_argument('--model');p.add_argument('--ffmpeg');p.add_argument('--plan',action='store_true');p.add_argument('--offline',action='store_true');p.add_argument('--wheelhouse')
    try:
        args=p.parse_args()
        if args.plan:result=setup(args)
        else:
            with lock(state_dir(args.state_dir)/'.runtime-setup-lock'):result=setup(args)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except Exception as exc:print(json.dumps({'ok':False,'error':str(exc),'action':'保留已下载文件；修复网络/权限/空间后重跑相同自动配置入口'},ensure_ascii=False),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
