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
# 剪映 11.7 实测：屏幕上一个汉字的宽度 ≈ fontSize × 画布高 / 1080（横竖版同一 fontSize，竖版大 1.78 倍）。
# 所以版式先按“占画面短边多少”设计，再换算回 fontSize，任何分辨率和画幅观感一致。
# 竖版数值是已在剪映看过、用户认可的 9:16 fontSize；横版数值是 1080 高画面上的目标像素。
VERTICAL_FS={'sub':32,'title':39,'cover':53,'label':20,'cover_label':18}
HORIZONTAL_PX={'sub':52,'title':46,'cover':76,'label':24,'cover_label':22}
LINE_H=1.57  # 剪映导入后每行（含标题底色内边距）高度 ≈ 1.57 × 字宽，实测
def _num(v):
 v=round(v,1);return int(v) if v==int(v) else v
def text_px(s,px):
 """估算一行文字宽度：汉字及全角符号 1 个字宽，ASCII 约 0.55 个。"""
 return sum(0.3 if ch==' ' else 0.55 if ord(ch)<0x2E80 else 1.0 for ch in s)*px
def layout_for(w,h):
 w,h=int(w),int(h);vertical=h>w;short=min(w,h)
 px={k:v*(1920/1080)*short/1080 for k,v in VERTICAL_FS.items()} if vertical else {k:v*short/1080 for k,v in HORIZONTAL_PX.items()}
 fs={k:_num(v*1080/h) for k,v in px.items()}
 lay={'vertical':vertical,'px':px,'sub_size':fs['sub'],'title_size':fs['title'],'cover_size':fs['cover'],
      'label_size':fs['label'],'cover_label_size':fs['cover_label']}
 if vertical:
  lay.update(sub_y=-21.9,title_y=32.8,title_x=0,title_anchor='center',cover_y=0,cover_label_y=19,
             bar_h=max(10,round(short*44/1080)),sub_chars=int(.69*w/px['sub']),
             title_chars=int(.78*w/px['title']),cover_chars=int(.88*w/px['cover']),label_chars=int(.87*w/px['label']))
 else:
  # 横屏人脸通常在画面正中偏上：内容标题放左上角，不压头顶；字幕在下三分之一；封面标签紧贴封面上方。
  # 横屏人眼多在上三分之一线附近：封面参考压到中线偏下，避开脸；小标签放封面下方。
  cover_y=-7;cover_half=LINE_H*px['cover']/h*100;gap=(LINE_H*px['cover_label']/2+.012*h)/h*100
  # 接近方形（4:3、1:1）时左上角太窄，改为顶部居中。
  wide=w/h>=1.5
  lay.update(sub_y=-35,title_anchor='top-left' if wide else 'top-center',title_margin=(.045*w,.055*h),
             cover_y=cover_y,cover_label_y=round(cover_y-cover_half-gap,2),bar_h=max(10,round(short*40/1080)),
             sub_chars=int(.65*w/px['sub']),title_chars=int((.40 if wide else .78)*w/px['title']),
             cover_chars=int(.58*w/px['cover']),label_chars=min(40,int(.87*w/px['label'])))
 return lay
def title_position(lay,lines,w,h):
 """内容标题位置（FCPXML 单位：画面高度的百分比，原点在中心）。"""
 if lay['title_anchor']=='center':return lay['title_x'],lay['title_y']
 px=lay['px']['title'];left,top=lay['title_margin']
 box_w=max(text_px(s,px) for s in lines)+.72*px;box_h=len(lines)*LINE_H*px
 x=round((left+box_w/2-w/2)/h*100,2) if lay['title_anchor']=='top-left' else 0
 return x,round(50-(top+box_h/2)/h*100,2)
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
 def title(t0,t1,text,name,lane,y,size,extra=None,x=0):
  nonlocal n
  # 起止都取最近帧：前一条的终点与后一条的起点相同时落在同一帧，不会叠 1 帧被导入器拆到两条轨上。
  t0=max(Fraction(0),round(Fraction(str(t0))/frame)*frame);t1=min(total,round(Fraction(str(t1))/frame)*frame)
  if t1<=t0:t1=min(total,t0+frame)
  if t1<=t0:return
  h=host(t0);n+=1
  el=ET.SubElement(h['node'],'title',ref=effect,lane=str(lane),name=name,offset=ts(h['start']+t0-h['offset']),start='0s',duration=ts(t1-t0))
  ET.SubElement(ET.SubElement(el,'text'),'text-style',ref=f'cyts{n}').text=text
  ET.SubElement(ET.SubElement(el,'text-style-def',id=f'cyts{n}'),'text-style',{'font':'Arial','fontSize':str(size),'fontColor':'1 1 1 1','alignment':'center',**(extra or {})})
  ET.SubElement(el,'adjust-transform',position=f'{_num(x)} {y}')
 for cap in captions:title(cap['start'],cap['end'],cap['text'],'字幕',1,lay['sub_y'],lay['sub_size'],SHADOW)
 for key,limit in (('content_title',lay['title_chars']),('cover_title',lay['cover_chars'])):
  if any(len(s)>limit for s in copy[key]):notes.append(f'{key} 有一行超过 {limit} 字，需缩短或核对版式')
 tx,ty=title_position(lay,copy['content_title'],width,height)
 title(0,total,'\n'.join(copy['content_title']),'内容标题',2,ty,lay['title_size'],dict(SHADOW,bold='1',backgroundColor='0 0 0 0.55'),x=tx)
 title(0,min(5,total),'\n'.join(copy['cover_title']),'封面标题参考',6,lay['cover_y'],lay['cover_size'],dict(SHADOW,bold='1'))
 title(0,min(5,total),'封面标题参考','封面标题参考（可删除）',7,lay['cover_label_y'],lay['cover_label_size'],SHADOW)
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
  if len(label)>lay['label_chars']:notes.append('章节总字数过多，需要缩短，不能换成文字图片')
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
