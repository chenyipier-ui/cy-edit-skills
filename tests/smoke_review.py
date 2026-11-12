"""Synthetic release regression; no model download or human listening claim."""
from pathlib import Path
import ast,json,sys,argparse,subprocess,os
base=Path(__file__).resolve().parents[1]/'skills';work=Path(os.environ['CY_TEST_OUTPUT']);checks=[]
os.environ['CY_EDIT_EXPERIENCE_DIR']=str(work/'final-feedback')
for p in base.glob('cy-edit-*/scripts/*.py'):ast.parse(p.read_text(),filename=str(p))
checks.append('全部Python语法')
for p in base.glob('cy-edit-*/**/*.sh'):subprocess.run(['bash','-n',str(p)],check=True)
checks.append('Mac启动脚本语法')
sys.path.insert(0,str(base/'cy-edit-shortvideo/scripts'));import editor
folder=work/'short';plan=folder/'plan.json'
editor.command_compile_multi(argparse.Namespace(plan=str(plan),out=str(folder/'delivery-r4'),force=False,max_chars=None,keep_punct=False,corrections=None,no_recheck=True,backend='auto',model=None))
xml=next((folder/'delivery-r4').glob('*.fcpxml'));dtd=os.environ.get('CY_TEST_FCPXML_DTD')
if dtd:
 subprocess.run(['xmllint','--noout','--dtdvalid',dtd,str(xml)],check=True);checks.append('短视频最小计划及FCPXML1.10 DTD')
from cy_quality import write_review,record_review,frame_bounds
assert frame_bounds(1.01,1.99,[(1.01,1.99)],25,0,3)==(25,50)
try:frame_bounds(1.01,2.01,[(1.01,1.99)],25,1.01,3)
except ValueError:checks.append('帧对齐保护词头词尾，拒绝扩入已删邻句')
else:raise AssertionError('unsafe aligned edge accepted')
review=work/'review-final';write_review(review,[{'key':'a','text':'这是一条完整信息','start':0,'end':1}]);d=json.loads((review/'review-decisions-template.json').read_text());d['read_all']=True;d['checks']={k:True for k in d['checks']};editor.atomic_write_json(review/'decision.json',d)
assert record_review(review,review/'decision.json')['content_review']=='recorded_pass'
write_review(review,[{'key':'a','text':'这是修改后的新内容','start':0,'end':2}]);assert json.loads((review/'review-result.json').read_text())['content_review']=='awaiting_content_review'
try:record_review(review,review/'decision.json')
except ValueError:checks.append('改稿使旧复查失效，旧结论保留历史')
else:raise AssertionError('stale review accepted')
# Both installers ship byte-identical shared modules.
for f in ('cy_quality.py','cy_packaging.py','lve_common.py','lve_feedback.py','lve_feedback_flow.py'):
 assert (base/'cy-edit-shortvideo/scripts'/f).read_bytes()==(base/'cy-edit-longvideo/scripts'/f).read_bytes()
checks.append('两套共享模块一致')
(work/'final-structure.json').write_text(json.dumps({'passed':checks,'windows':'not_run'},ensure_ascii=False,indent=2))
print(checks)
