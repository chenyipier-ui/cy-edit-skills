"""One-call feedback capture over the existing versioned ledger and receipts."""
from pathlib import Path
import uuid
from lve_common import digest, lock, now, project_at, read_json, require, write_json, shared_experience_data
from lve_feedback import context, adopt, open_turn, close_turn, record_feedback, visible_entries


def refresh_adoption(directory, data, task, overrides=None):
    root,project=project_at(directory);ctx=context(directory,data)
    old=read_json(root/'experience/adoption.json') if (root/'experience/adoption.json').exists() else {'decisions':[]}
    old_by_id={d['experience_id']:d for d in old['decisions']}
    overrides=overrides or []; supplied={d['experience_id']:d for d in overrides}
    entries={e['id']:e for e in visible_entries(ctx)}
    require(len(supplied)==len(overrides) and set(supplied)<=set(entries),'采用决定包含重复或失效经验，请重新读取')
    conflicts={x for ids in ctx['conflicts'].values() for x in ids};decisions=[]
    for eid,e in entries.items():
        old_item=old_by_id.get(eid)
        if eid in supplied:
            d=dict(supplied[eid])
        elif old_item and old_item.get('entry_sha256')==digest(e) and eid not in conflicts:
            d={k:old_item[k] for k in ('experience_id','decision','reason','application') if k in old_item}
        else:
            d={'experience_id':eid,'decision':'pending','reason':'新经验或变更项，待按本项目目的判断，尚未应用'}
        if eid in conflicts:
            d={'experience_id':eid,'decision':'pending','reason':'偏好冲突，先按用户当前要求处理并解决冲突'}
        decisions.append(d)
    request={'context_sha256':ctx['context_sha256'],'task_context':task or project['name'],'decisions':decisions}
    path=root/'experience/requests'/(digest(request)[:20]+'.json');write_json(path,request)
    result=adopt(directory,path,data)
    return ctx,decisions,result


def experience(directory, data, task=None, decisions_file=None):
    data=shared_experience_data(data)
    overrides=read_json(decisions_file).get('decisions',[]) if decisions_file else []
    ctx,decisions,result=refresh_adoption(directory,data,task,overrides)
    return {'message':'已读取经验。沿用仍有效的项目选择；新增或有冲突的项先列为待判断。',
            'entries':[{'id':e['id'],'scope':e['scope'],'lesson':e['lesson'],'applies_when':e['applies_when'],
                        'does_not_apply_when':e['does_not_apply_when'],'verification':e['verification']} for e in visible_entries(ctx)],
            'decisions':decisions,'record':result['md']}


def feedback(directory, input_file, data):
    data=shared_experience_data(data)
    root,project=project_at(directory);raw=read_json(input_file)
    if 'message_id' in raw and 'items' not in raw:
        return record_feedback(directory,input_file,data)  # Advanced legacy contract.
    require(isinstance(raw.get('message'),str) and raw['message'].strip(),'message 填本轮用户原话')
    items=raw.get('items');require(isinstance(items,list) and items,'逐项填写本轮意见，至少一项')
    with lock(root/'.feedback-request-lock'):
        # Persist a local receipt token into the Agent-authored input. Retrying a
        # copied input keeps its identity; a NEW feedback round may repeat words.
        raw=read_json(input_file)
        content={k:v for k,v in raw.items() if k!='request_id'}
        legacy_key=digest([project['id'],content])[:24]
        if not raw.get('request_id'):
            legacy=root/'feedback/requests'/legacy_key/'request.json'
            raw['request_id']='legacy:'+legacy_key if legacy.exists() else 'request-'+uuid.uuid4().hex
            write_json(input_file,raw)
        token=raw['request_id'];require(isinstance(token,str) and token.strip(),'request_id 由工具自动生成；新一轮请使用新的反馈输入')
        key=legacy_key if token=='legacy:'+legacy_key else digest([project['id'],token])[:24]
        folder=root/'feedback/requests'/key
        raw=content
        journal=folder/'request.json'
        if journal.exists():
            job=read_json(journal);require(job['input_sha256']==digest(raw),'请求内容变化，请使用新文件')
        else:
            job={'turn_id':'local-'+uuid.uuid4().hex[:20],'input_sha256':digest(raw),'raw':raw,'created_at':now()}
            write_json(journal,job)
        tid=job['turn_id'];message_ids=[f'{tid}:item{i}' for i in range(1,len(items)+1)]
        turn={'turn_id':tid,'user_message_ids':[tid],'feedback_message_ids':message_ids}
        write_json(folder/'turn.json',turn);open_turn(directory,folder/'turn.json')
        saved=[]
        for index,item in enumerate(items):
            text=item.get('text');require(isinstance(text,str) and text.strip(),'每项 text 填对应用户原话')
            scope=item.get('scope','project');operation=item.get('operation','record')
            if scope=='user':require(item.get('authorization'),'长期偏好需要用户明确“以后”等授权原话')
            event={'message_id':message_ids[index],'user_feedback':text,'category':item.get('category','correction'),'scope':scope,
                   'problem':item.get('problem',text),'action':item.get('action','已保存，等待按意见修订'),
                   'lesson':item.get('lesson',text),'applies_when':item.get('applies_when','本项目当前修订' if scope=='project' else '需按实际项目判断'),
                   'does_not_apply_when':item.get('does_not_apply_when','用户另有明确要求或不符合本项适用范围'),
                   'verification':item.get('verification','pending'),'operation':operation}
            for field in ('preference_key','authorization','target_ids','evidence'):
                if field in item:event[field]=item[field]
            path=folder/f'item-{index+1}.json';write_json(path,event)
            saved.append(record_feedback(directory,path,data))
            write_json(folder/'progress.json',{'saved_count':len(saved),'expected_count':len(items),'saved':saved,'updated_at':now()})
        ctx,decisions,_=refresh_adoption(directory,data,raw.get('task_context') or project['name'])
        receipt=close_turn(directory,tid,data)
        pending=sum(d['decision']=='pending' for d in decisions)
        return {'message':f'本轮 {len(saved)} 条意见已保存；修订和效果确认单独记录。',
                'feedback_saved':receipt['feedback_saved'],'saved_count':len(saved),'missing':receipt['missing'],
                'experience_pending':pending,'project_record':str(root/'feedback/反馈记录.md'),
                'experience_record_current':receipt['adoption']['status']=='current',
                'receipt':str(root/'feedback/turn-receipts'/f'{tid}.md'),
                'next':'按本轮意见修改；经验待判断项只在确认适用后采用。' if receipt['complete'] else '意见已保存；先运行 experience 刷新经验清单，再按实际适用项修改。',
                'local_turn_id':tid,'id_note':'本工具生成的本地记录号，不是聊天平台消息 ID'}
