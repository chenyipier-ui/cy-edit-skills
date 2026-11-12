"""Synthetic release regression; no model download or human listening claim."""
from pathlib import Path
import os,sys,json,argparse,subprocess
base=Path(__file__).resolve().parents[1]/'skills';work=Path(os.environ['CY_TEST_OUTPUT']);folder=work/'short';folder.mkdir(parents=True,exist_ok=True)
os.environ['CY_EDIT_EXPERIENCE_DIR']=str(folder/'experience')
sys.path.insert(0,str(base/'cy-edit-shortvideo/scripts'))
import editor
source=work/'fixture.mp4';meta=editor.normalized_probe(source)
editor.atomic_write_json(folder/'meta.json',meta)
srt=folder/'source.srt';srt.write_text('1\n00:00:01,000 --> 00:00:05,000\n先理清内容主线\n\n2\n00:00:06,000 --> 00:00:10,000\n再删掉重复表达\n',encoding='utf-8')
editor.command_import_srt(argparse.Namespace(srt=str(srt),source=str(source),out=str(folder/'transcript.json'),language='zh'))
t=json.loads((folder/'transcript.json').read_text());ids=[u['id'] for u in editor.transcript_units(t,int(t['duration_ms']))]
plan={'schema_version':1,'kind':'jianying_multisource_plan','project':{'name':'口播合成验收'},'sources':{'A':{'source_meta':'meta.json','transcript':'transcript.json'}},
      'content_title':['先理清主线','再删重复表达'],'cover_title':['剪短一点','真的讲清了吗'],'chapters':[],
      'segments':[{'source':'A','evidence_unit_ids':ids,'reason':'合成验收保留这两句'}]}
p=folder/'plan.json';editor.atomic_write_json(p,plan)
editor.command_compile_multi(argparse.Namespace(plan=str(p),out=str(folder/'delivery'),force=False,max_chars=None,keep_punct=False,corrections=None,no_recheck=True,backend='auto',model=None))
assert len(list((folder/'delivery').iterdir()))==1
print("short minimal plan compiled into one FCPXML; ASR explicitly skipped for this synthetic test")
