"""Re-transcribe the actual PCM edit, never the source transcript or planned text."""
from fractions import Fraction
from pathlib import Path
from lve_common import require,write_json
from cy_quality import compare_asr,retranscribe

def recheck_asr(preview,output,project,config,folder):
 backend=config.get('backend');model=config.get('model_path') or config.get('model')
 require(backend in ('mlx','faster') and model,'复转写后端未准备，不能将跳过记为通过')
 prompt='普通话口语，请照实记录重复、嗯、呃、然后、这个等语气词。使用简体中文。'
 if backend=='mlx':
  import mlx_whisper
  transcribe=lambda path:mlx_whisper.transcribe(str(path),path_or_hf_repo=str(model),language='zh',word_timestamps=True,initial_prompt=prompt,condition_on_previous_text=False)
 else:
  from lve_transcribe import faster_engine
  engine,device,note=faster_engine(model,config.get('device','auto'),False)
  def transcribe(path):
   segs,_=engine.transcribe(str(path),language='zh',word_timestamps=True,vad_filter=False,initial_prompt=prompt,condition_on_previous_text=False)
   return {'segments':[{'start':s.start,'end':s.end,'text':s.text,'words':[{'start':w.start,'end':w.end,'word':w.word} for w in (s.words or [])]} for s in segs]}
 result=retranscribe(preview,Path(folder)/'re-asr',transcribe)
 write_json(Path(folder)/'成片再转写.json',result);fps=Fraction(project['timeline']['fps'])
 def where(t):
  s=next((s for s in output['segments'] if float(s['timeline_in_frame']/fps)<=t<float((s['timeline_in_frame']+s['duration_frames'])/fps)),output['segments'][-1])
  return {'timeline_seconds':t,'source_id':s['source_id'],'source_seconds':s['in_seconds']+t-float(s['timeline_in_frame']/fps),'segment':s['id']}
 return compare_asr(output['captions'],result,where)
