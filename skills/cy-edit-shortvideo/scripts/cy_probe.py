"""PyAV fallback for clean machines without a system ffprobe."""
from pathlib import Path

def probe(path):
 import av
 with av.open(str(path)) as c:
  streams=[]
  for s in c.streams:
   if s.type not in ('audio','video'):continue
   ctx=s.codec_context
   d={'index':s.index,'codec_type':s.type,'codec_name':ctx.name,
      'start_time':str(float(s.start_time*s.time_base)) if s.start_time is not None else '0',
      'duration':str(float(s.duration*s.time_base)) if s.duration is not None else None,
      'disposition':{'default':int(bool(s.disposition.value&1))},'tags':dict(s.metadata)}
   if s.type=='video':
    d.update(width=ctx.width,height=ctx.height,pix_fmt=ctx.format.name if ctx.format else None,
             avg_frame_rate=str(s.average_rate or 0),r_frame_rate=str(s.base_rate or s.average_rate or 0),nb_frames=s.frames)
   else:d.update(sample_rate=ctx.sample_rate,channels=ctx.channels,channel_layout=ctx.layout.name)
   streams.append(d)
  # First displayed frame can expose rotation metadata absent from stream tags.
  if c.streams.video:
   try:
    f=next(c.decode(video=0));rotation=getattr(f,'rotation',0)
    if rotation:
     v=next(x for x in streams if x['codec_type']=='video');v['side_data_list']=[{'rotation':rotation}]
   except (StopIteration,av.error.FFmpegError):pass
  return {'streams':streams,'format':{'duration':str(c.duration/av.time_base) if c.duration else None,
          'start_time':str((c.start_time or 0)/av.time_base),'format_name':c.format.name}}

def audio_duration(path):
 import av
 with av.open(str(path)) as c:
  return round(sum(f.samples/f.sample_rate for f in c.decode(audio=0))*1000)
