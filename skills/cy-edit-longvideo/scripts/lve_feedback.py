"""Append-only feedback, cross-project preference changes, adoption and turn receipts."""
from __future__ import annotations
from pathlib import Path
from lve_common import atomic_text, digest, identifier, lock, md_text, now, project_at, read_json, require, sha_file, write_json

FIELDS = ('message_id', 'user_feedback', 'category', 'scope', 'problem', 'action',
          'lesson', 'applies_when', 'does_not_apply_when', 'verification')

def targets(event):
    return event.get('target_ids') or ([event['supersedes']] if event.get('supersedes') else [])

def inactive_ids(events):
    return {target for e in events for target in targets(e)}

def active_events(events):
    removed = inactive_ids(events)
    return [e for e in events if e['id'] not in removed and e.get('operation', 'record') != 'revoke'
            and e['verification'] != 'rejected']

def render(events, title, universe=None):
    universe = events if universe is None else universe
    removed = inactive_ids(universe)
    lines = [f'# {title}', '', 'JSON 为原始记录，MD 为生成视图；请通过反馈命令更新。',
             '当前要求优先。撤销保留审计历史，不是删除原话。不要随安装包分发。', '']
    for e in events:
        status = '已失效／被替代' if e['id'] in removed else e.get('operation', 'record')
        lines += [f"## {e['id']} · {status}", '', f"- 时间：{e['recorded_at']}",
                  f"- 项目：{md_text(e['project_name'])}（{e['project_id']}）",
                  f"- 来源消息：{md_text(e['message_id'])}", f"- 类型／范围：{e['category']}／{e['scope']}",
                  f"- 偏好键：{e.get('preference_key') or '未设置'}",
                  f"- 方案版本：{e.get('proposal_sha256') or '尚未确认方案'}", '', '**用户反馈**', '']
        lines += ['> ' + x for x in e['user_feedback'].splitlines()]
        for label, key in [('问题', 'problem'), ('采取的改动', 'action'), ('经验', 'lesson'),
                           ('适用条件', 'applies_when'), ('不适用／例外', 'does_not_apply_when'),
                           ('验证状态', 'verification')]:
            lines += ['', f'**{label}：** {e[key]}']
        lines += ['', f"**证据：** {e.get('evidence') or '尚无验证证据'}",
                  f"**替代／撤销对象：** {', '.join(targets(e)) or '无'}",
                  f"**变更授权：** {e.get('authorization') or '普通反馈记录'}", '']
    return '\n'.join(lines) + '\n'

def load_all(root, data):
    global_path = data / 'experience' / 'events.json'
    local_path = root / 'feedback' / 'events.json'
    events = read_json(global_path) if global_path.exists() else []
    known = {e['id']: e for e in events}
    for e in read_json(local_path) if local_path.exists() else []:
        if e['id'] in known:
            require(digest(e) == digest(known[e['id']]), '项目和经验库有同 ID 不同内容，停止自动覆盖')
        else:
            events.append(e); known[e['id']] = e
    return events

def persist(root, project, data, events):
    local = [e for e in events if e['project_id'] == project['id']]
    # Global ledger is the recovery source if a later local/Markdown write fails.
    write_json(data / 'experience' / 'events.json', events)
    write_json(root / 'feedback' / 'events.json', local)
    atomic_text(data / 'experience' / '经验库.md', render(events, '剪辑经验库（长视频与单主题口播共用）'))
    atomic_text(root / 'feedback' / '反馈记录.md', render(local, '项目反馈与修订经验', events))

def selection(events, project_id):
    active = active_events(events)
    groups = {}
    for e in active:
        if e['scope'] == 'user' and e.get('preference_key'):
            groups.setdefault(e['preference_key'], []).append(e['id'])
    return {'current_project': [e for e in active if e['project_id'] == project_id],
            'user_preferences': [e for e in active if e['scope'] == 'user' and e['project_id'] != project_id],
            'candidate_lessons': [e for e in active if e['scope'] == 'candidate' and e['project_id'] != project_id],
            'conflicts': {k: v for k, v in groups.items() if len(v) > 1},
            'legacy_preferences_without_key': [e['id'] for e in active if e['scope'] == 'user' and not e.get('preference_key')]}

def visible_entries(ctx):
    return [e for key in ('current_project', 'user_preferences', 'candidate_lessons') for e in ctx[key]]

def snapshot(ctx):
    return digest({'entries': sorted((e['id'], digest(e)) for e in visible_entries(ctx)), 'conflicts': ctx['conflicts']})

def adoption_status(root, ctx):
    path = root / 'experience' / 'adoption.json'
    if not path.exists():
        return {'status': 'missing', 'message': '先记录采用／不采用／待确认的经验清单'}
    doc = read_json(path)
    md = root / 'experience' / '本项目经验采用清单.md'
    if not md.exists() or sha_file(md) != doc.get('md_sha256'):
        return {'status': 'incomplete', 'message': '采用清单 MD 与 JSON 未完整同步；重试 adopt-experiences'}
    by_id = {e['id']: e for e in visible_entries(ctx)}
    invalid = [d['experience_id'] for d in doc['decisions']
               if d['decision'] == 'adopt' and (d['experience_id'] not in by_id or
                  d['entry_sha256'] != digest(by_id[d['experience_id']]))]
    return {'status': 'current' if doc['context_sha256'] == snapshot(ctx) else 'stale',
            'invalid_adopted_ids': invalid, 'path': str(path),
            'message': '旧清单保留历史；已失效条目立即停止应用，刷新采用清单'}

def record_feedback(directory, event_file, data_dir):
    root, project = project_at(directory); data = Path(data_dir)
    raw = read_json(event_file)
    require(isinstance(raw, dict), '反馈必须是 JSON 对象')
    for key in FIELDS:
        require(isinstance(raw.get(key), str) and raw[key].strip(), f'反馈缺少 {key}')
    require(raw['scope'] in ('project', 'user', 'candidate'), 'scope 无效')
    require(raw['category'] in ('requirement', 'correction', 'preference', 'acceptance'), 'category 无效')
    require(raw['verification'] in ('pending', 'applied_unverified', 'verified', 'rejected'), 'verification 无效')
    if raw['verification'] == 'verified':
        require(isinstance(raw.get('evidence'), str) and raw['evidence'].strip(), 'verified 需要真实证据说明')
    operation = raw.get('operation', 'record')
    require(operation in ('record', 'update', 'revoke'), 'operation 无效')
    refs = targets(raw)
    require(isinstance(refs, list) and all(isinstance(x, str) for x in refs) and len(refs) == len(set(refs)), '目标 ID 必须唯一')
    require(not (raw.get('supersedes') and raw.get('target_ids')), '不能同时使用 supersedes 和 target_ids')
    if raw['scope'] == 'user':
        require(isinstance(raw.get('preference_key'), str) and raw['preference_key'].strip(), '长期偏好需稳定 preference_key；旧记录可作为变更目标')
        require(isinstance(raw.get('authorization'), str) and raw['authorization'].strip(), '长期偏好需要用户明确授权原话，不能把单项目要求自动推广')
    require(not refs or raw['verification'] != 'rejected', '被否定的尝试不能使旧经验失效；请另记有效撤销')
    if operation in ('update', 'revoke'):
        require(refs and raw['verification'] != 'rejected', '更新／撤销需有效目标，不能用 rejected 使旧偏好失效')
        require(isinstance(raw.get('authorization'), str) and raw['authorization'].strip(), '更新／撤销需实际用户授权原话')
    else:
        require(not raw.get('target_ids'), '跨项目变更使用 update/revoke')
    event_id = 'fb-' + digest([project['id'], raw['message_id']])[:16]
    with lock(data / '.feedback-lock'), lock(root / '.feedback-lock'):
        events = load_all(root, data); by_id = {e['id']: e for e in events}
        previous = by_id.get(event_id)
        if previous:
            require(previous['input_sha256'] == digest(raw), '同一消息 ID 内容改变；用新 ID 关联旧条目')
            event = previous
        else:
            active_ids = {e['id'] for e in active_events(events)}
            for target_id in refs:
                require(target_id in by_id, '目标经验不存在；先 context 取真实 ID')
                target = by_id[target_id]
                require(target_id in active_ids, '目标已失效；先刷新 context，不能意外恢复旧偏好')
                if target['project_id'] != project['id']:
                    require(operation in ('update', 'revoke') and target['scope'] == raw['scope'] == 'user',
                            '跨项目只允许显式更新／撤销 user 偏好；临时要求不迁移')
                if target['scope'] == 'user':
                    require(raw['scope'] == 'user' and operation in ('update', 'revoke'), '长期偏好使用明确 update/revoke')
                    require(not target.get('preference_key') or target['preference_key'] == raw['preference_key'], '偏好键与目标不同')
            event = dict(raw, id=event_id, operation=operation, project_id=project['id'], project_name=project['name'],
                         recorded_at=now(), proposal_sha256=(project.get('approval') or {}).get('proposal_sha256'), input_sha256=digest(raw))
            events.append(event)
        persist(root, project, data, events)
        paths = [root/'feedback/events.json', root/'feedback/反馈记录.md', data/'experience/events.json', data/'experience/经验库.md']
        receipt = {'id': event_id, 'message_id': raw['message_id'], 'saved_at': now(), 'saved': True,
                   'files': {str(p): sha_file(p) for p in paths}, 'note': '写入时的收据；全局库后续新增反馈会改变其哈希'}
        write_json(root/'feedback/receipts'/f'{event_id}.json', receipt)
    return {'id': event_id, 'new': previous is None, 'saved': True, 'project_md': str(paths[1]),
            'experience_md': str(paths[3]), 'scope': event['scope'], 'operation': operation,
            'receipt': str(root/'feedback/receipts'/f'{event_id}.json')}

def context(directory, data_dir):
    root, project = project_at(directory); data = Path(data_dir)
    with lock(data/'.feedback-lock'), lock(root/'.feedback-lock'):
        events = load_all(root, data)
        persist(root, project, data, events)
        ctx = selection(events, project['id'])
        ctx['context_sha256'] = snapshot(ctx)
        ctx['adoption'] = adoption_status(root, ctx)
    ctx['instruction'] = '筛选适用条件，记录每项采用决策；pending 不等于验证成功；冲突不可静默应用；MD 是生成视图。'
    return ctx

def adopt(directory, input_file, data_dir):
    root, project = project_at(directory); data = Path(data_dir); raw = read_json(input_file)
    with lock(data/'.feedback-lock'), lock(root/'.feedback-lock'):
        ctx = selection(load_all(root, data), project['id'])
        require(raw.get('context_sha256') == snapshot(ctx), '经验库已变化，重新 context 后再决定')
        require(isinstance(raw.get('task_context'), str) and raw['task_context'].strip(), '需要本项目适用情境')
        decisions = raw.get('decisions'); available = {e['id']: e for e in visible_entries(ctx)}
        require(isinstance(decisions, list), 'decisions 必须是列表；没有经验时允许空列表')
        ids = [d.get('experience_id') for d in decisions]
        require(len(set(ids)) == len(ids) and set(ids) == set(available), '对本次返回的每项经验记录 adopt/skip/pending，不遗漏不伪造')
        conflict_ids = {i for group in ctx['conflicts'].values() for i in group}
        for d in decisions:
            require(d.get('decision') in ('adopt', 'skip', 'pending') and d.get('reason'), '缺少采用决定或理由')
            require(d.get('decision') != 'adopt' or d['experience_id'] not in conflict_ids, '冲突偏好先解决或记 pending')
            require(d.get('decision') != 'adopt' or d.get('application'), '采用时写明具体影响哪个内容剪辑动作')
            e = available[d['experience_id']]
            d.update(entry_sha256=digest(e), source_project=e['project_name'], lesson=e['lesson'],
                     verification=e['verification'], scope=e['scope'])
        record = dict(raw, recorded_at=now(), project_id=project['id'])
        revision = digest(record)[:20]
        lines = ['# 本项目经验采用清单', '', f"情境：{raw['task_context']}", f"经验快照：{raw['context_sha256']}",
                 '新反馈／跨项目撤销后须刷新；旧清单不是继续应用失效偏好的授权。', '']
        for d in decisions:
            lines += [f"## {d['experience_id']} · {d['decision']}", f"- 来源：{d['source_project']}；{d['verification']}",
                      f"- 经验：{d['lesson']}", f"- 理由：{d['reason']}", f"- 应用：{d.get('application') or '不应用／待决定'}", '']
        if not decisions: lines += ['本次没有可用历史经验，按当前要求执行。']
        atomic_text(root/'experience/本项目经验采用清单.md', '\n'.join(lines)+'\n')
        record['md_sha256'] = sha_file(root/'experience/本项目经验采用清单.md')
        write_json(root/'experience/adoptions'/f'{revision}.json', record)
        write_json(root/'experience/adoption.json', record)
    return {'revision': revision, 'md': str(root/'experience/本项目经验采用清单.md')}

def open_turn(directory, input_file):
    root, _ = project_at(directory); raw = read_json(input_file)
    tid = identifier(raw.get('turn_id')); messages = raw.get('user_message_ids'); expected = raw.get('feedback_message_ids')
    require(isinstance(messages, list) and messages and all(isinstance(x, str) and x for x in messages), '登记本轮所有用户消息 ID')
    require(isinstance(expected, list) and all(isinstance(x, str) and x for x in expected) and len(set(expected)) == len(expected), '登记所有反馈子项 ID')
    if not expected: require(bool(raw.get('no_feedback_reason')), '没有反馈时说明判断依据')
    path = root/'feedback/turns'/f'{tid}.json'
    with lock(root/'.feedback-lock'):
        if path.exists():
            require(read_json(path)['input_sha256'] == digest(raw), '本轮登记已存在；补充消息用新的 turn_id，不能删掉待保存项')
        else: write_json(path, dict(raw, opened_at=now(), input_sha256=digest(raw)))
    return {'turn_id': tid, 'registered_feedback': expected, 'status': 'open', 'path': str(path)}

def close_turn(directory, turn_id, data_dir):
    root, project = project_at(directory); data = Path(data_dir); identifier(turn_id)
    turn = read_json(root/'feedback/turns'/f'{turn_id}.json')
    with lock(data/'.feedback-lock'), lock(root/'.feedback-lock'):
        events = load_all(root, data)
        persist(root, project, data, events)
        local = {e['message_id']: e for e in events if e['project_id'] == project['id']}
        missing = [m for m in turn['feedback_message_ids'] if m not in local]
        saved = [{'message_id': m, 'event_id': local[m]['id']} for m in turn['feedback_message_ids'] if m in local]
        ctx = selection(events, project['id']); adoption = adoption_status(root, ctx)
        complete = not missing and adoption['status'] == 'current'
        paths = [root/'feedback/events.json', root/'feedback/反馈记录.md', data/'experience/events.json', data/'experience/经验库.md']
        report = {'turn_id': turn_id, 'closed_at': now(), 'feedback_saved': not missing, 'complete': complete,
                  'expected': turn['feedback_message_ids'], 'saved': saved, 'missing': missing, 'adoption': adoption,
                  'files': {str(p): sha_file(p) for p in paths},
                  'limit': '只核对已登记的消息；无后台监听，不能发现 Agent 完全未登记的反馈。'}
        write_json(root/'feedback/turn-receipts'/f'{turn_id}.json', report)
        atomic_text(root/'feedback/turn-receipts'/f'{turn_id}.md', '# 本轮反馈保存回执\n\n'+
                    f"轮次：{turn_id}\n\n已保存：{len(saved)} / {len(turn['feedback_message_ids'])}\n\n"+
                    f"未保存：{', '.join(missing) or '无'}\n\n经验采用清单：{adoption['status']}\n\n"+
                    f"本轮闭环：{'完成' if complete else '待补齐'}\n\n"+
                    '\n'.join(f"- {p} · SHA-256 `{h}`" for p,h in report['files'].items())+'\n\n'+report['limit']+'\n')
    return report
