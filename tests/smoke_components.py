"""Synthetic release regression; no model download or human listening claim."""
from pathlib import Path
import sys,tempfile,json,os,importlib.util,subprocess
base=Path(__file__).resolve().parents[1]/'skills'
work=Path(os.environ['CY_TEST_OUTPUT'])
sys.path.insert(0,str(base/'cy-edit-longvideo/scripts'))
from cy_quality import safe_ranges,word_chars,write_review,record_review
from lve_common import write_json
from lve_feedback_flow import feedback,experience
folder=work/'components';folder.mkdir(parents=True,exist_ok=True);os.environ['CY_EDIT_EXPERIENCE_DIR']=str(folder/'shared')
checks=[]
assert word_chars([{'word':'一个方法','start':1,'end':2}])==[(c,1.,2.) for c in '一个方法'];checks.append('多字词未伪造逐字时间')
try:safe_ranges(0,4,[],[(1,2)],[(1.2,1.7)])
except ValueError:checks.append('拒绝切入词内')
else:raise AssertionError('inside word accepted')
assert safe_ranges(0,4,[],[(.1,.9),(3,3.5)],compress=True)[0][1]>3.5;checks.append('不把无转写的有声间隙自动认作咳嗽删除')
r=write_review(folder/'review',[{'key':'a','text':'客户最终要看到实际结果','start':0,'end':2},{'key':'b','text':'客户需要看到实际结果','start':3,'end':5}]);template=json.loads((folder/'review/review-decisions-template.json').read_text());assert r['pairs']>0
write_json(folder/'decisions.json',template)
try:record_review(folder/'review',folder/'decisions.json')
except ValueError:checks.append('重复疑点未给理由时拒绝闭环')
else:raise AssertionError('empty reason accepted')
p=folder/'project';p.mkdir(exist_ok=True);write_json(p/'project.json',{'schema_version':3,'id':'fixture_feedback','name':'合成反馈验收'})
def send(name,text,**kw):
 raw={'message':text,'items':[{'text':text,'scope':'user','authorization':text,'lesson':text,'preference_key':'subtitle.style',**kw}]};f=folder/(name+'.json');write_json(f,raw);return feedback(p,f,folder/'legacy')
a=send('one','以后字幕用白字阴影');assert a['feedback_saved'] is True
ctx=experience(p,folder/'legacy');eid=ctx['entries'][0]['id']
b=send('two','以后字幕改成白字细阴影',operation='update',target_ids=[eid]);assert b['feedback_saved'] is True
ctx=experience(p,folder/'legacy');assert eid not in [e['id'] for e in ctx['entries']]
eid2=ctx['entries'][0]['id'];send('three','撤销刚才的字幕偏好',operation='revoke',target_ids=[eid2])
assert not experience(p,folder/'legacy')['entries'];checks.append('反馈MD回执、跨项目偏好更新撤销与停止采用')
# Same feedback file retried from a second filename must recover instead of duplicate.
q=folder/'copy.json';q.write_bytes((folder/'one.json').read_bytes());feedback(p,q,folder/'legacy')
events=json.loads((folder/'shared/experience/events.json').read_text());assert len(events)==3;checks.append('反馈重试不重复保存')
repeat=send('repeat-new-turn','以后字幕用白字阴影');assert repeat['feedback_saved']
events=json.loads((folder/'shared/experience/events.json').read_text());assert len(events)==4;checks.append('新一轮相同原话仍单独保存，不与重试混淆')
from lve_sources import inspect_media
import shutil
config={'ffmpeg':shutil.which('ffmpeg'),'backend':'none'}
source=work/'fixture.mp4'
from lve_project import init_project,import_transcript,propose,approve
from lve_audio import ensure_audio,cached_map
from lve_assist import build_plan
from lve_media import export_project,verify_delivery
proj=folder/'long';init_project(proj,[str(source)],'长视频合成流程',config)
manifest=json.loads((proj/'project.json').read_text());sid=next(iter(manifest['sources']))
srt=folder/'fixture.srt';srt.write_text('1\n00:00:01,000 --> 00:00:05,000\n先理清内容主线\n\n2\n00:00:06,000 --> 00:00:10,000\n再删掉重复表达\n',encoding='utf-8')
import_transcript(proj,srt,sid);p2=json.loads((proj/'project.json').read_text());p2['preparation_complete']=True;write_json(proj/'project.json',p2)
cached_map(ensure_audio(proj,p2['sources'][sid],config));units=json.loads((proj/'transcript.json').read_text())['units'];uids=[u['id'] for u in units]
cand=lambda id,ids:{'id':id,'title':'剪辑方法','thesis':'先主线后删重复','reason':'合成验收夹具','unit_ids':ids,'excluded':[], 'content_title':['先理清主线','再删除重复'],'cover_title':['口播剪短','就能讲清楚吗'],'chapters':[]}
write_json(folder/'proposal.json',{'mode':'multi','reviewed_sources':[sid],'candidates':[cand('one',uids),cand('two',uids[:1])]})
propose(proj,folder/'proposal.json');reply=folder/'fixture-authorization.txt';reply.write_text('合成验收用，授权生成两条，未声称真人访谈内容验收通过。');approve(proj,'one,two',reply)
plan=proj/'plans/v1.json';build_plan(proj,plan);result=export_project(proj,plan,'v1',config,skip_recheck=True);verify_delivery(proj/'deliveries/v1')
assert len(list((proj/'deliveries/v1').iterdir()))==1;checks.append('长视频多条合成计划生成单个多项目FCPXML与内部复查')
# Native FFprobe-independent inspection uses the same file in the candidate short skill.
sys.path.insert(0,str(base/'cy-edit-shortvideo/scripts'))
from cy_probe import probe
info=probe(source);assert info['streams'][0]['width']==320;checks.append('无ffprobe时PyAV探测路线可用')
write_json(folder/'results.json',{'checks':checks,'limits':['合成组件验收，不是口播内容主任务留出测试','Windows未实机运行','成片ASR在独立子步骤验证'],'status':'passed'})
print(json.dumps({'passed':checks},ensure_ascii=False))
