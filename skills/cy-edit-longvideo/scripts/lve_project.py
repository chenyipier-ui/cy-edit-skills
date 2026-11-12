"""Projects, concise proposals and explicit authorization. Media clocks are elapsed seconds."""
from __future__ import annotations
import math
import re
import uuid
from pathlib import Path
from lve_common import atomic_text,digest,identifier,now,project_at,read_json,require,sha_file,write_json
from lve_sources import check_sources,collect_inputs,content_sample,ensure_v2,inspect_media,prepare_sources,quick_fingerprint,sources_digest

DELIVERY={'type':'clean_fcpxml','original_audio':True,'subtitles':True,'subtitle_mode':'embedded_only','editor':'jianying'}
DEFAULT_EDITING={'compress':True,'threshold_seconds':.6,'head_seconds':.15,'tail_seconds':.2,'punctuation':'spoken','max_chars_vertical':13,'max_chars_horizontal':24}


def init_project(directory,media,name,config,input_list=None,fps=None,normalization='reference',brief=None):
    root=Path(directory).expanduser().resolve();records=collect_inputs(media,input_list)
    request={'records':records,'name':name,'fps':fps,'normalization':'reference'};pending=root/'initializing.json'
    if (root/'project.json').exists():
        _,project=project_at(root);ensure_v2(project)
        require(project.get('input_request')==request,'项目参数改变；请用新目录保留原版')
        check_sources(project);return {'project':str(root),'resumed':True}
    if root.exists() and any(root.iterdir()):require(pending.exists() and read_json(pending)==request,'目录非空，请使用新项目目录')
    root.mkdir(parents=True,exist_ok=True);write_json(pending,request)
    sources,timeline=prepare_sources(root,records,config,fps,'reference')
    if brief and brief.get('canvas'):
        for key in ('width','height'):
            value=brief['canvas'].get(key);require(type(value) is int and 240<=value<=8192,'画布宽高需为 240–8192 的整数');timeline[key]=value
    project={'schema_version':3,'id':uuid.uuid4().hex,'name':name,'created_at':now(),'input_request':request,
             'sources':sources,'sources_sha256':sources_digest(sources),'timeline':timeline,'brief':brief or {},
             'status':'analysis','approval':None,'preparation_complete':False}
    write_json(root/'project.json',project);pending.unlink(missing_ok=True)
    atomic_text(root/'素材索引.md','# 素材索引\n\n默认只链接原片。输入文件数量和输出条数相互独立。时间从每份素材视频起点计。\n\n'+
        '\n'.join(f"- {sid} · {s['label']} · {s['original']['duration_seconds']:.2f} 秒 · {s['original']['path']}" for sid,s in sources.items())+'\n')
    return {'project':str(root),'source_count':len(sources),'timeline':timeline,'message':'已登记原片，不转码视频；下一步抽音频并整理内容。'}


def srt_units(text):
    pattern=re.compile(r'(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})')
    result=[]
    for block in re.split(r'\n\s*\n',text.strip().replace('\r','')):
        m=pattern.search(block);require(m,'存在无法解析的 SRT 块')
        values=[int(x) for x in m.groups()]
        def sec(v):return v[0]*3600+v[1]*60+v[2]+v[3]/1000
        result.append({'id':f'u{len(result)+1:05d}','start':sec(values[:4]),'end':sec(values[4:]),'text':block[m.end():].strip(),'speaker':'unknown','timing':'imported_srt'})
    return result


def store_transcript(root,project,units,provenance):
    ensure_v2(project);require(isinstance(units,list),'转写需要 units 数组');ids=set()
    for u in units:
        identifier(u.get('id'));require(u['id'] not in ids,'原文编号重复');ids.add(u['id'])
        sid=u.get('source_id');require(sid in project['sources'],'转写来源不存在');a,b=u.get('start'),u.get('end')
        require(type(a) in (int,float) and type(b) in (int,float) and math.isfinite(a) and math.isfinite(b),'时间必须为有限秒数')
        require(0<=a<b<=project['sources'][sid]['original']['duration_seconds']+.05,'转写超出原片时长')
        require(isinstance(u.get('text'),str) and u['text'].strip(),'转写文字为空');u.setdefault('speaker','unknown')
    document={'schema_version':3,'sources_sha256':project['sources_sha256'],'provenance':provenance,
              'units':sorted(units,key=lambda u:(u['source_id'],u['start'],u['end'],u['id']))}
    path=Path(root)/'transcript.json'
    if path.exists():
        old=read_json(path)
        if old.get('units')==document['units'] and old.get('sources_sha256')==document['sources_sha256']:
            project['transcript_sha256']=digest(old);write_json(Path(root)/'project.json',project)
            from lve_assist import packets
            return {'unchanged':True,'units':len(units),'transcript_sha256':digest(old),'reading':packets(root)}
        if digest(old)!=digest(document):write_json(Path(root)/'history'/('transcript-'+digest(old)[:16]+'.json'),old)
    write_json(path,document);project['transcript_sha256']=digest(document);write_json(Path(root)/'project.json',project)
    atomic_text(Path(root)/'原始转写.md','# 原始转写\n\n优先阅读分份阅读索引。文字未经人工核实。\n\n'+
        '\n\n'.join(f"[{u['id']}] {u['source_id']} {u['start']:.3f}–{u['end']:.3f}s · {u['speaker']}\n{u['text']}" for u in document['units'])+'\n')
    from lve_assist import packets
    return {'units':len(units),'transcript_sha256':digest(document),'reading':packets(root)}


def import_transcript(directory,filename,source_id=None):
    root,project=project_at(directory);check_sources(project);file=Path(filename).expanduser().resolve()
    if file.suffix.lower()=='.srt':
        source_id=source_id or (next(iter(project['sources'])) if len(project['sources'])==1 else None)
        require(source_id in project['sources'],'多素材导入 SRT 需要 source-id')
        units=srt_units(file.read_text(encoding='utf-8-sig'))
        for u in units:u.update(id=source_id+'_'+u['id'],source_id=source_id)
        old=read_json(root/'transcript.json')['units'] if (root/'transcript.json').exists() else []
        old_by_id={u['id']:u for u in old}
        for u in units:
            previous=old_by_id.get(u['id'])
            if previous and all(previous.get(k)==u.get(k) for k in ('start','end','text')):u['speaker']=previous.get('speaker','unknown')
        units=[u for u in old if u['source_id']!=source_id]+units
    else:
        supplied=read_json(file);require(supplied.get('sources_sha256')==project['sources_sha256'],'转写 JSON 来源不符');units=supplied.get('units')
    imported_sources={source_id} if source_id else {u['source_id'] for u in units}
    for sid in imported_sources:
        project.setdefault('prepared_sources',{})[sid]={'source_identity':project['sources'][sid]['original']['identity'],'status':'imported_unverified'}
    project['preparation_complete']=set(project.get('prepared_sources',{}))==set(project['sources'])
    return store_transcript(root,project,units,{'type':'imported','input_sha256':sha_file(file)})


def normalize_proposal(project,transcript,raw):
    units={u['id']:u for u in transcript['units']};rows=raw.get('candidates');require(isinstance(rows,list) and rows,'至少提供一个候选')
    mode=raw.get('mode','single' if len(rows)==1 else 'multi');require(mode in ('single','multi','both'),'mode 使用 single、multi 或 both')
    reviewed=raw.get('reviewed_sources',[]);excluded_sources=raw.get('excluded_sources',{})
    require(isinstance(reviewed,list) and isinstance(excluded_sources,dict),'reviewed_sources 用数组，excluded_sources 用对象')
    require(set(reviewed)<=set(project['sources']) and set(excluded_sources)<=set(project['sources']),'审阅声明中有不存在的素材')
    candidates=[];ids=set()
    for n,item in enumerate(rows,1):
        c=dict(item);c['id']=identifier(c.get('id','main' if len(rows)==1 else f'topic-{n:02d}'))
        require(c['id'] not in ids,'候选编号重复');ids.add(c['id'])
        for key in ('title','thesis','reason'):require(isinstance(c.get(key),str) and c[key].strip(),f'候选需要 {key}')
        refs=c.get('unit_ids');require(isinstance(refs,list) and refs and len(refs)==len(set(refs)) and set(refs)<=set(units),'保留编号为空、重复或不存在')
        require(isinstance(c.get('excluded'),list),'excluded 用数组说明删除了什么；没有则 []')
        c.setdefault('script_a','\n\n'.join(units[u]['text'] for u in refs));c.setdefault('risks',[]);c.setdefault('cuts',[]);c.setdefault('keep_pause_units',[])
        require(isinstance(c['risks'],list) and isinstance(c['cuts'],list) and isinstance(c['keep_pause_units'],list),'risks、cuts、keep_pause_units 均使用数组')
        if c.get('hard_max_seconds') is not None:
            require(type(c['hard_max_seconds']) in (int,float) and math.isfinite(c['hard_max_seconds']) and c['hard_max_seconds']>0,'硬时长必须是正秒数')
        require(set(c['keep_pause_units'])<=set(refs),'保留停顿的编号须属于本候选')
        for cut in c['cuts']:
            require(cut.get('unit_id') in refs and cut.get('reason'),'段内删减需要本候选原文编号与原因')
            u=units[cut['unit_id']];a,b=cut.get('start'),cut.get('end')
            require(type(a) in (int,float) and type(b) in (int,float) and math.isfinite(a) and math.isfinite(b) and u['start']<=a<b<=u['end'],'段内删减需要原片内真实秒数且落在该句内')
        c['estimated_seconds']=round(sum(units[u]['end']-units[u]['start'] for u in refs),2)
        c.setdefault('status','ready');require(c['status'] in ('ready','needs_material','not_recommended'),'候选状态无效')
        candidates.append(c)
    brief=project.get('brief',{});overrides=raw.get('editing',{})
    require(isinstance(overrides,dict) and set(overrides)<=set(DEFAULT_EDITING),'editing 包含不支持的字段，请按示例填写')
    editing=dict(DEFAULT_EDITING,**overrides)
    require(type(editing['compress']) is bool and editing['punctuation'] in ('spoken','keep'),'停顿开关或字幕标点模式无效')
    return {'schema_version':3,'sources_sha256':project['sources_sha256'],'transcript_sha256':project['transcript_sha256'],
            'mode':mode,'audience':raw.get('audience',brief.get('audience','根据素材与当前要求判断')),'purpose':raw.get('purpose',brief.get('purpose','保留核心信息，清楚连贯地表达')),
            'recommendation':raw.get('recommendation',candidates[0]['reason']),'reviewed_sources':reviewed,'excluded_sources':excluded_sources,
            'delivery':DELIVERY,'editing':editing,'candidates':candidates}


def render_proposal(project,transcript,proposal):
    rows=['# 剪辑方案','',f"给谁看：{proposal['audience']}",f"为什么做：{proposal['purpose']}",'',f"建议：{proposal['recommendation']}",'',
          '预计时长来自选中原文，不含最终停顿压缩；剪辑稿不创造原声中不存在的发言。','',
          '默认清理已测出的多余低能量停顿；情绪、思考和关键回应中的停顿可以保留。语气词与重来只删除方案明确列出的部分。','']
    for n,c in enumerate(proposal['candidates'],1):
        rows += [f"## {n}．{c['title']}",'',f"主线：{c['thesis']}",f"理由：{c['reason']}",f"原文时长合计：约 {c['estimated_seconds']:.1f} 秒",'',c['script_a'],'',
                 '删除：'+('；'.join(map(str,c['excluded'])) or '没有另外列出的内容删减'),
                 '段内删减：'+('；'.join(f"{x['unit_id']} {x['start']}–{x['end']}s：{x['reason']}" for x in c['cuts']) or '无'),
                 '保留停顿的原文：'+(', '.join(c['keep_pause_units']) or '由当前语境判断，无特别指定'),
                 '待确认：'+('；'.join(map(str,c['risks'])) or '导入后的视听效果仍需查看'),'']
    rows += ['## 怎么回复','','例如：“按推荐做成一条，第二段删掉，最后那次停顿保留。”用户不需要填写编号或技术文件。','',
             '## 来源附录','','以下供回溯，日常反馈不要求用户处理编号。','']
    units={u['id']:u for u in transcript['units']}
    for c in proposal['candidates']:
        rows += [f"### {c['id']} · {c['title']}"]
        rows += [f"- {uid} · {units[uid]['source_id']} {units[uid]['start']:.3f}–{units[uid]['end']:.3f}s · {units[uid]['text']}" for uid in c['unit_ids']]
    return '\n'.join(rows)+'\n'


def propose(directory,filename):
    root,project=project_at(directory);check_sources(project);t=read_json(root/'transcript.json')
    require(digest(t)==project.get('transcript_sha256'),'转写被手改，请用 import-transcript 版本化更新')
    raw=read_json(filename);proposal=normalize_proposal(project,t,raw);stamp=digest(proposal)
    write_json(root/'proposals'/(stamp+'.json'),proposal);write_json(root/'proposal.json',proposal)
    atomic_text(root/'方案审核.md',render_proposal(project,t,proposal))
    return {'message':'方案已整理，请用自然语言选择或修改。','review':str(root/'方案审核.md'),'candidates':[{'id':c['id'],'title':c['title']} for c in proposal['candidates']]}


def approve(directory,selected,evidence_file,solo_speaker=None):
    root,project=project_at(directory);ensure_v2(project);evidence=Path(evidence_file).read_text(encoding='utf-8').strip();require(evidence,'需要实际用户确认原话')
    proposal=read_json(root/'proposal.json');t=read_json(root/'transcript.json')
    # Audience and purpose can be inferred; no opening questionnaire is required.
    require(project.get('preparation_complete'),'仍在准备或转写；先读完整片再确认方案')
    require(proposal['transcript_sha256']==digest(t)==project['transcript_sha256'] and proposal['sources_sha256']==project['sources_sha256'],'方案来源已改变，请重新 propose')
    require(set(proposal['reviewed_sources'])|set(proposal['excluded_sources'])==set(project['sources']),'须读完每份输入或说明整份排除理由')
    require(all(str(x).strip() for x in proposal['excluded_sources'].values()),'排除来源需说明理由')
    ids=[x.strip() for x in selected.split(',')];candidates={c['id']:c for c in proposal['candidates']}
    require(ids and len(set(ids))==len(ids) and set(ids)<=set(candidates),'所选候选不存在或重复')
    require(proposal['mode']!='single' or len(ids)==1,'单条方案只选择一条')
    units={u['id']:u for u in t['units']}
    for cid in ids:
        c=candidates[cid];require(c['status']=='ready','所选候选仍有关键材料缺口')
        require({units[u]['source_id'] for u in c['unit_ids']}<=set(proposal['reviewed_sources']),'不能采用未读完或已排除的素材')
    if solo_speaker:
        from lve_assist import label_speaker
        # This flag is a declaration from the actual reply, never inferred from file count.
        selected_sources=sorted({units[u]['source_id'] for cid in ids for u in candidates[cid]['unit_ids']})
        label_speaker(root,solo_speaker,evidence_file,selected_sources,None)
        _,project=project_at(root);t=read_json(root/'transcript.json');proposal['transcript_sha256']=project['transcript_sha256']
        write_json(root/'proposal.json',proposal)
    if project.get('approval'):write_json(root/'history'/('approval-'+digest(project['approval'])[:16]+'.json'),project['approval'])
    record={'proposal_sha256':digest(proposal),'selected':ids,'confirmed_at':now(),'user_evidence':evidence}
    project.update(approval=record,status='approved');write_json(root/'project.json',project)
    write_json(root/'approved-proposal.json',proposal);write_json(root/'approval.json',record)
    atomic_text(root/'approved-review.md',render_proposal(project,t,proposal))
    return {'message':'已保存方案选择，接下来生成工程、字幕和试听版。','selected':ids}


def relink(directory,new_media,source_id,kind='working',reply_file=None):
    root,project=project_at(directory);ensure_v2(project);require(source_id in project['sources'],'来源不存在')
    m=project['sources'][source_id][kind];path=Path(new_media).expanduser().resolve();require(path.is_file(),'新媒体不存在')
    new=quick_fingerprint(path);require(content_sample(new)==content_sample(m['fingerprint']),'大小或首尾内容不符，不是同一素材')
    old_path=Path(m['path']);baseline=m.get('sha256')
    if not baseline and old_path.is_file():baseline=sha_file(old_path)
    value=sha_file(path)
    if baseline:require(value==baseline,'完整哈希不符，不能重关联')
    else:
        require(reply_file,'旧路径与完整哈希基线都不可用；先告知用户只能抽样匹配，提供确认原话后再关联')
        require(Path(reply_file).read_text(encoding='utf-8').strip(),'关联确认原话为空')
    old=m['path'];m.update(path=str(path),fingerprint=new,sha256=value)
    source=project['sources'][source_id]
    if source['mapping']['type']=='reference_original':
        source['original'].update(path=str(path),fingerprint=new,sha256=value);source['working'].update(path=str(path),fingerprint=new,sha256=value)
    project.setdefault('media_path_history',[]).append({'source_id':source_id,'old':old,'new':str(path),'at':now(),'verification':'full_hash_matched' if baseline else 'sample_match_user_confirmed_new_baseline'})
    write_json(root/'project.json',project)
    return {'message':'素材已重新关联，请重新 build 生成新路径工程。','full_baseline_matched':bool(baseline)}
