"""Shared editable FCPXML typography and a continuously filling bottom bar.
Text is never rasterized. Bar assets contain colour only and remain project dependencies.
"""
from __future__ import annotations
from fractions import Fraction
from pathlib import Path
import math,struct,zlib,xml.etree.ElementTree as ET

VERSION='1.0'
SHADOW={'shadowColor':'0 0 0 0.75','shadowOffset':'3 315','shadowBlurRadius':'5'}
def ts(v):
 v=Fraction(v);return f'{v.numerator}s' if v.denominator==1 else f'{v.numerator}/{v.denominator}s'
def layout_for(w,h):
 vertical=h>w
 return {'vertical':vertical,'sub_y':-21.9 if vertical else -36,'sub_size':32,
         'title_y':32.8 if vertical else 38,'title_size':39 if vertical else 36,
         'cover_size':53 if vertical else 50,'cover_label_y':19 if vertical else 24,
         'bar_h':max(10,round(h*(44/1920 if vertical else 30/1080))),
         'label_size':20 if vertical else 18,'title_chars':12 if vertical else 16,
         'cover_chars':10 if vertical else 16,'sub_chars':13 if vertical else 24}
def png(path,w,h,rgba):
 def chunk(tag,data):return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
 raw=(b'\0'+bytes(rgba)*w)*h
 data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw,9))+chunk(b'IEND',b'')
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return path

def validate_copy(copy,known,total):
 for name in ('content_title','cover_title'):
  v=copy.get(name)
  if not isinstance(v,list) or len(v)!=2 or not all(isinstance(s,str) and s.strip() for s in v):raise ValueError(name+' 需要两行文案；先通读最终内容再写')
 if ''.join(copy['content_title'])==''.join(copy['cover_title']):raise ValueError('内容标题和封面标题参考不能相同')
 chapters=copy.get('chapters',[])
 if total>120 and not chapters:raise ValueError('超过两分钟需写章节名与起点；起点用保留段或原文编号')
 prev=-1
 for c in chapters:
  if not isinstance(c,dict) or not str(c.get('title','')).strip() or c.get('start') not in known:raise ValueError('章节起点不在最终保留的内容中')
  t=known[c['start']]
  if t<=prev:raise ValueError('章节起点需要按成片顺序递增')
  prev=t

def decorate(xml,clips,captions,copy,folder,width,height,frame):
 """clips: node,start,offset,duration,id,unit_ids. captions: start/end seconds, text."""
 width,height=int(width),int(height);frame=Fraction(frame);lay=layout_for(width,height)
 res=xml.find('resources');total=max((c['offset']+c['duration'] for c in clips),default=Fraction(0));known={}
 for c in clips:
  known.setdefault(c['id'].split('.')[0],float(c['offset']))
  for u in c.get('unit_ids',[]):known.setdefault(u,float(c['offset']))
 validate_copy(copy,known,float(total));notes=[];n=0
 effect='cyTitle'
 ET.SubElement(res,'effect',id=effect,name='Basic Title',uid='.../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti')
 def host(t):return next(c for c in clips if c['offset']<=t<c['offset']+c['duration'])
 def title(t0,t1,text,name,lane,y,size,extra=None):
  nonlocal n
  t0=max(Fraction(0),math.floor(Fraction(str(t0))/frame)*frame);t1=min(total,math.ceil(Fraction(str(t1))/frame)*frame)
  if t1<=t0:return
  h=host(t0);n+=1
  el=ET.SubElement(h['node'],'title',ref=effect,lane=str(lane),name=name,offset=ts(h['start']+t0-h['offset']),start='0s',duration=ts(t1-t0))
  ET.SubElement(ET.SubElement(el,'text'),'text-style',ref=f'cyts{n}').text=text
  ET.SubElement(ET.SubElement(el,'text-style-def',id=f'cyts{n}'),'text-style',{'font':'Arial','fontSize':str(size),'fontColor':'1 1 1 1','alignment':'center',**(extra or {})})
  ET.SubElement(el,'adjust-transform',position=f'0 {y}')
 for cap in captions:title(cap['start'],cap['end'],cap['text'],'字幕',1,lay['sub_y'],lay['sub_size'],SHADOW)
 for key,limit in (('content_title',lay['title_chars']),('cover_title',lay['cover_chars'])):
  if any(len(s)>limit for s in copy[key]):notes.append(f'{key} 有一行超过 {limit} 字，需缩短或核对版式')
 title(0,total,'\n'.join(copy['content_title']),'内容标题',2,lay['title_y'],lay['title_size'],dict(SHADOW,bold='1',backgroundColor='0 0 0 0.55'))
 title(0,min(5,total),'\n'.join(copy['cover_title']),'封面标题参考',6,0,lay['cover_size'],dict(SHADOW,bold='1'))
 title(0,min(5,total),'封面标题参考','封面标题参考（可删除）',7,lay['cover_label_y'],18,SHADOW)
 if total>120:
  begin=Fraction(str(known[copy['chapters'][0]['start']]))
  begin=math.floor(begin/frame)*frame
  if begin>=total:raise ValueError('进度条起点越界')
  h=host(begin);bh=lay['bar_h'];y=round(-50+bh/height*50,5)
  # Full-width colour rectangles slide from outside the canvas. Visible width increases
  # continuously; no crop or unsupported independent X scaling is necessary.
  for aid,rgba,lane,name in (('cyBarBG',(235,235,235,110),3,'进度条底'),('cyBarFill',(28,28,28,230),4,'进度条填充')):
   path=png(Path(folder)/(aid+f'_{width}x{bh}.png'),width,bh,rgba)
   ET.SubElement(res,'format',id=aid+'Fmt',width=str(width),height=str(bh))
   asset=ET.SubElement(res,'asset',id=aid,name=name,start='0s',duration=ts(total),hasVideo='1',format=aid+'Fmt')
   if xml.get('version')=='1.8':asset.set('src',path.resolve().as_uri())
   else:ET.SubElement(asset,'media-rep',kind='original-media',src=path.resolve().as_uri())
   el=ET.SubElement(h['node'],'video',ref=aid,lane=str(lane),name=name,start='0s',offset=ts(h['start']+begin-h['offset']),duration=ts(total-begin))
   x=-width/height*100 if lane==4 else 0
   tr=ET.SubElement(el,'adjust-transform',position=f'{x} {y}')
   if lane==4:
    anim=ET.SubElement(ET.SubElement(tr,'param',name='position'),'keyframeAnimation')
    ET.SubElement(anim,'keyframe',time='0s',value=f'{x} {y}',interp='linear',curve='linear')
    ET.SubElement(anim,'keyframe',time=ts(total-begin),value=f'0 {y}',interp='linear',curve='linear')
  label='｜'.join(c['title'] for c in copy['chapters'])
  title(begin,total,label,'进度条章节',5,y,lay['label_size'],SHADOW)
  if len(label)>(26 if lay['vertical'] else 40):notes.append('章节总字数过多，需要缩短，不能换成文字图片')
 return notes

def combine(documents):
 """One exchange file, distinct projects. All ID references are namespaced."""
 import copy
 root=ET.Element('fcpxml',version='1.10');res=ET.SubElement(root,'resources');library=ET.SubElement(root,'library')
 for n,doc in enumerate(documents,1):
  doc=copy.deepcopy(doc);ids={e.get('id'):f'p{n}_{e.get("id")}' for e in doc.iter() if e.get('id')}
  for el in doc.iter():
   for attr in ('id','ref','format'):
    if el.get(attr) in ids:el.set(attr,ids[el.get(attr)])
  for el in doc.find('resources'):res.append(el)
  for el in doc.find('library'):library.append(el)
 return root

def serialize(root):
 ET.indent(root,space='  ')
 return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'+ET.tostring(root,encoding='unicode')+'\n'
