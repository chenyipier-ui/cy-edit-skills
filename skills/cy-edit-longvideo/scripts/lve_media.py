"""Draft-first exchange projects with editable text, SRT, listening audio and separate machine measurements."""
from __future__ import annotations
import csv
import io
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from lve_common import atomic_text,digest,identifier,now,project_at,read_json,require,sha_file,write_json
from lve_sources import check_sources


def time_string(value):
    v=Fraction(value);return f'{v.numerator}s' if v.denominator==1 else f'{v.numerator}/{v.denominator}s'


def srt_time(ms):
    h,rem=divmod(ms,3600000);m,rem=divmod(rem,60000);s,n=divmod(rem,1000);return f'{h:02}:{m:02}:{s:02},{n:03}'


def render_srt(project,output):
    fps=Fraction(project['timeline']['fps']);rows=[]
    for n,c in enumerate(output['captions'],1):
        a=round(Fraction(c['in_frame']*1000,1)/fps);b=round(Fraction(c['out_frame']*1000,1)/fps)
        require(b>a,'字幕毫秒时长无效');rows.append(f'{n}\n{srt_time(a)} --> {srt_time(b)}\n{c["text"]}\n')
    return '\n'.join(rows)


def validate_plan(directory,plan_file):
    root,p=project_at(directory);sources=check_sources(p);plan=read_json(plan_file)
    require(plan.get('schema_version')==3,'此版本需要按秒定位的 v3 计划，请重新 build')
    require(set(plan)<= {'schema_version','sources_sha256','transcript_sha256','proposal_sha256','outputs','quality','warnings','review_records','generation_signature'},'计划有未支持字段；请按当前示例重新生成')
    approval=p.get('approval');require(approval,'尚未确认方案');proposal=read_json(root/'approved-proposal.json');t=read_json(root/'transcript.json')
    require(plan.get('sources_sha256')==p['sources_sha256'],'媒体登记已改变')
    require(plan.get('proposal_sha256')==approval['proposal_sha256']==digest(proposal),'不是当前确认方案')
    require(plan.get('transcript_sha256')==p['transcript_sha256']==digest(t)==proposal['transcript_sha256'],'转写已改变')
    outputs=plan.get('outputs');require(isinstance(outputs,list) and outputs,'没有输出')
    ids=[o['id'] for o in outputs];require(len(ids)==len(set(ids)) and set(ids)==set(approval['selected']),'输出与用户选择不符')
    signature=digest(outputs);require(plan.get('generation_signature')==signature,'机械计划已被手改；修改原方案或字幕修正表后重新 build')
    generated=root/'generated-plans'/(signature+'.json');require(generated.exists(),'缺少生成记录，请用 build-plan 或 build 重新生成')
    require(read_json(generated)=={'outputs_sha256':signature,'proposal_sha256':approval['proposal_sha256'],'transcript_sha256':p['transcript_sha256']},'生成记录不是当前来源')
    units={u['id']:u for u in t['units']};candidates={c['id']:c for c in proposal['candidates']};fps=Fraction(p['timeline']['fps'])
    for output in outputs:
        require(set(output)<= {'id','title','segments','captions','edits','duration_frames','packaging'},'输出字段不符；支持剪辑、字幕、标题章节与删减记录')
        candidate=candidates[output['id']];require(output['title']==candidate['title'],'标题不符');total=0;seen=[];segment_ids=set()
        for s in output['segments']:
            require(set(s)== {'id','source_id','in_seconds','out_seconds','unit_ids','timeline_in_frame','duration_frames'},'片段字段不符')
            identifier(s['id']);require(s['id'] not in segment_ids,'片段编号重复');segment_ids.add(s['id'])
            sid=s['source_id'];require(sid in sources,'未知素材');a,b=s['in_seconds'],s['out_seconds']
            require(type(a) in (int,float) and type(b) in (int,float) and 0<=a<b<=sources[sid]['working']['duration_seconds']+.05,'片段时间越界')
            count=s['duration_frames'];require(type(count) is int and count>0 and s['timeline_in_frame']==total,'成片时间轴不连续')
            require(abs(float((Fraction(str(b))-Fraction(str(a)))*fps)-count)<.001,'秒数与成片时长不符')
            total+=count
            for uid in s['unit_ids']:
                require(uid in candidate['unit_ids'] and units[uid]['source_id']==sid,'引用未批准原文')
                require(units[uid]['start']<b and units[uid]['end']>a,'原文与媒体区间无交集')
                if uid not in seen:seen.append(uid)
        require(seen==candidate['unit_ids'],'缺段或次序与方案不符');require(output['duration_frames']==total,'总时长不符')
        require(candidate.get('hard_max_seconds') is None or total/float(fps)<=candidate['hard_max_seconds'],'超过硬时长，请修订方案')
        last=0
        for c in output['captions']:
            require(set(c)== {'in_frame','out_frame','text','unit_ids'},'字幕字段不符')
            a,b=c['in_frame'],c['out_frame'];require(type(a) is int and type(b) is int and last<=a<b<=total,'字幕倒序或越界');last=b
            require(c['text'].strip() and '\n' not in c['text'] and '\x00' not in c['text'],'字幕需单行有效文字')
            require(set(c['unit_ids'])<=set(candidate['unit_ids']),'字幕来源不符')
        require(output['captions'],'所选语音没有字幕，请检查')
    quality=plan.get('quality',{});require(set(quality)== {'content','speaker','boundary','captions'},'缺少质量状态')
    reviewed={key:False for key in quality}
    for r in plan.get('review_records',[]):
        require(r.get('evidence'),'回听记录需要实际依据')
        if r.get('scope')=='all':
            for key in r['checks']:require(key in reviewed,'未知检查项');reviewed[key]=True
    require(quality==reviewed,'核对开关没有对应的实际记录；可直接导出待回听草稿，不必补造通过')
    return root,p,plan,plan.get('warnings',[])


def xml_document(project,output,asset_dir):
    fps=Fraction(project['timeline']['fps']);frame=1/fps
    xml=ET.Element('fcpxml',version='1.10');resources=ET.SubElement(xml,'resources')
    ET.SubElement(resources,'format',id='projectFormat',frameDuration=time_string(frame),width=str(project['timeline']['width']),height=str(project['timeline']['height']))
    refs={}
    for n,sid in enumerate(dict.fromkeys(s['source_id'] for s in output['segments']),1):
        source=project['sources'][sid];m=source['working'];fid=f'f{n}';rid=f'a{n}';refs[sid]=rid
        ET.SubElement(resources,'format',id=fid,frameDuration=time_string(1/Fraction(m['fps'])),width=str(m['width']),height=str(m['height']))
        attrs={'id':rid,'name':m['name'],'start':time_string(Fraction(str(m['start_seconds']))),
               'duration':time_string(Fraction(str(m['duration_seconds']))),'hasVideo':'1','format':fid}
        if m['audios']:
            attrs.update(hasAudio='1',audioSources=str(len(m['audios'])),audioRate=str(m['audios'][0]['sample_rate']))
            channels={a['channels'] for a in m['audios']}
            if len(channels)==1:attrs['audioChannels']=str(next(iter(channels)))
        asset=ET.SubElement(resources,'asset',attrs);ET.SubElement(asset,'media-rep',kind='original-media',src=Path(m['path']).as_uri())

    event=ET.SubElement(ET.SubElement(xml,'library'),'event',name='长内容剪辑')
    node=ET.SubElement(event,'project',name=output['title']);seq=ET.SubElement(node,'sequence',format='projectFormat',duration=time_string(output['duration_frames']*frame),tcStart='0s',tcFormat='NDF',audioLayout='stereo',audioRate='48k')
    spine=ET.SubElement(seq,'spine');hosts=[]
    for s in output['segments']:
        source=project['sources'][s['source_id']];m=source['working'];origin=Fraction(str(m['start_seconds']));source_start=origin+Fraction(round(Fraction(str(s['in_seconds']))*fps),1)/fps  # in_seconds 来自 帧号/fps 的浮点：还原到帧，避免 12366666666666667/1000000000000000s 这类 FCP 读不了的时间
        clip=ET.SubElement(spine,'asset-clip',ref=refs[s['source_id']],name=s['id'],offset=time_string(s['timeline_in_frame']*frame),start=time_string(source_start),duration=time_string(s['duration_frames']*frame))
        hosts.append({'node':clip,'id':s['id'],'unit_ids':s['unit_ids'],'start':source_start,'offset':s['timeline_in_frame']*frame,'duration':s['duration_frames']*frame})
        if len(m['audios'])>1:
            chosen=source['audio_stream_index'];start_channel=1
            for track in m['audios']:
                if track['index']==chosen:
                    ET.SubElement(clip,'audio-channel-source',srcCh=', '.join(str(c) for c in range(start_channel,start_channel+track['channels'])),role='dialogue');break
                start_channel+=track['channels']
    from cy_packaging import decorate
    captions=[{'start':float(c['in_frame']*frame),'end':float(c['out_frame']*frame),'text':c['text']} for c in output['captions']]
    notes=decorate(xml,hosts,captions,output.get('packaging',{}),asset_dir,project['timeline']['width'],project['timeline']['height'],frame)
    # audio-channel-source must follow anchored elements under asset-clip.
    for host in hosts:
        clip=host['node']
        for el in list(clip.findall('audio-channel-source')):clip.remove(el);clip.append(el)
    return xml,notes


PENDING_LISTEN='成片复查未完成，待回听'


def export_project(directory,plan_file,version,config,skip_recheck=False,skip_reason=None):
    identifier(version);root,p,plan,warnings=validate_plan(directory,plan_file);destination=root/'deliveries'/version
    require(not destination.exists(),'交付版本已存在，请使用新版本号')
    records=root/'records'/version;require(not records.exists(),'编译记录版本已存在，请用新版本号')
    records.mkdir(parents=True);destination.parent.mkdir(parents=True,exist_ok=True)
    from lve_feedback_flow import experience
    from lve_common import shared_experience_data
    experience_result=experience(root,shared_experience_data(config.get('state_dir')),p.get('brief',{}).get('purpose') or p['name'])
    write_json(records/'本次经验采用快照.json',experience_result)
    from cy_packaging import combine,serialize
    from cy_quality import write_review
    from lve_audio import build_preview,recheck
    from lve_final_asr import recheck_asr
    documents=[];reviews=[];issues=[];fps=Fraction(p['timeline']['fps'])
    report={'version':version,'status':'awaiting_content_review','outputs':[],'editor_import':'not_run','user_acceptance':'pending'}
    if skip_recheck:
        # 跳过不是通过：交付记录、复查文件和返回结果都写明待回听。
        report['recheck']={'status':'not_run','note':PENDING_LISTEN,'reason':skip_reason or '用户指定 --skip-recheck'}
    write_json(records/'executed-plan.json',plan);write_json(records/'source-manifest.json',p['sources'])
    for output in plan['outputs']:
        folder=records/output['id'];folder.mkdir()
        xml,notes=xml_document(p,output,root/'assets'/version/output['id']);documents.append(xml)
        atomic_text(folder/'字幕.srt',render_srt(p,output))
        measured={'status':'not_run'};asr={'status':'not_run'}
        if skip_recheck:asr=dict(report['recheck']);measured={'status':'not_run','note':PENDING_LISTEN}
        else:
            try:
                preview=folder/'试听.wav';preview_warnings=build_preview(root,p,output,config,preview)
                measured=recheck(preview,output,fps);measured.pop('acoustic',None);measured['preview_warnings']=preview_warnings
                asr=recheck_asr(preview,output,p,config,folder)
            except Exception as exc:
                asr={'status':'failed','error':str(exc)};issues.append(str(exc))
        rows=[{'key':','.join(c['unit_ids']),'text':c['text'],'start':float(c['in_frame']/fps),'end':float(c['out_frame']/fps)} for c in output['captions']]
        review=write_review(folder,rows,{'asr':asr,'audio':measured,'layout':notes,'source_warnings':warnings});reviews.append(review)
        report['outputs'].append({'id':output['id'],'title':output['title'],'duration_frames':output['duration_frames'],'review':review,'asr':asr.get('status')})
    filename='剪辑工程.fcpxml';content=serialize(combine(documents))
    stage=Path(tempfile.mkdtemp(prefix='.export-',dir=destination.parent))
    try:
        atomic_text(stage/filename,content);ET.parse(stage/filename)
        report['fcpxml_sha256']=sha_file(stage/filename);report['fcpxml']=str(destination/filename)
        write_json(records/'delivery.json',report);os.replace(stage,destination)
    finally:
        if stage.exists():shutil.rmtree(stage)
    pending=('## '+PENDING_LISTEN+'\n\n原因：'+report['recheck']['reason']+'。没有做成片试听复测与再转写，不能当作复查通过；交付前须实际回听，或环境就绪后用新版本号重新 build。\n\n') if skip_recheck else ''
    atomic_text(records/'导入与依赖.md','# 导入说明\n\n'+pending+'剪映首页本地草稿菜单 → 导入 PR 或 FCP 工程，选择交付目录唯一的 .fcpxml。多条方案在一个交换文件里保留多个独立项目；目标软件能否导入多个项目需看实测记录。\n\n所有文字是可编辑标题；封面标题参考只在前 5 秒。原片和 assets 下的纯色进度条图片必须保留，单个 XML 不内嵌视频。换电脑须迁移并重新关联素材。内部字幕、试听与复查不作为最终交付文件。\n')
    p['last_delivery']={'version':version,'path':str(destination),'status':report['status'],'records':str(records)}
    if skip_recheck:p['last_delivery']['recheck']=PENDING_LISTEN
    write_json(root/'project.json',p)
    result={'message':'工程已生成，正在复查最终保留内容。' if not skip_recheck else '工程已生成；'+PENDING_LISTEN+'（'+report['recheck']['reason']+'）。','status':report['status']}
    if skip_recheck:result['recheck']=report['recheck']
    return {**result,'fcpxml':str(destination/filename),'reviews':reviews,'issues':issues,'experience':experience_result}


def verify_delivery(directory):
    folder=Path(directory).resolve();record=folder.parent.parent/'records'/folder.name/'delivery.json';report=read_json(record)
    files=list(folder.iterdir());require(len(files)==1 and files[0].suffix=='.fcpxml','交付目录应只有一个 FCPXML')
    require(sha_file(files[0])==report['fcpxml_sha256'],'工程改变，与记录不符');ET.parse(files[0])
    return {'message':'单文件交付及 XML 结构一致；编辑器效果和完整听审需分别确认。'}
