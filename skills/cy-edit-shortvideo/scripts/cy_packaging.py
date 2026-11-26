"""Shared editable FCPXML typography and a continuously filling chapter bar.
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
HORIZONTAL_PX={'sub':54,'title':48,'cover':80,'label':28,'cover_label':24}
LINE_H=1.57  # 剪映导入后每行（含标题底色内边距）高度 ≈ 1.57 × 字宽，实测
def _num(v):
 v=round(v,1);return int(v) if v==int(v) else v
def text_px(s,px,ascii_width=.55):
 """估算一行文字宽度：汉字及全角符号 1 个字宽，ASCII 约 0.55 个。"""
 return sum(0.3 if ch==' ' else ascii_width if ord(ch)<0x2E80 else 1.0 for ch in s)*px
def layout_for(w,h):
 w,h=int(w),int(h);vertical=h>w;short=min(w,h)
 px={k:v*(1920/1080)*short/1080 for k,v in VERTICAL_FS.items()} if vertical else {k:v*short/1080 for k,v in HORIZONTAL_PX.items()}
 fs={k:_num(v*1080/h) for k,v in px.items()}
 lay={'vertical':vertical,'px':px,'sub_size':fs['sub'],'title_size':fs['title'],'cover_size':fs['cover'],
      'cover_label_size':fs['cover_label'],'bar_h':max(10,round(short*44/1080))}
 if vertical:
  lay.update(sub_y=-21.9,title_y=32.8,title_x=0,title_anchor='center',cover_y=0,cover_label_y=19,
             sub_chars=int(.69*w/px['sub']),title_chars=int(.78*w/px['title']),cover_chars=int(.88*w/px['cover']))
 else:
  # 横屏人脸通常在画面正中偏上：内容标题放左上角，不压头顶；字幕在下三分之一；封面标签紧贴封面上方。
  # 横屏人眼多在上三分之一线附近：封面参考压到中线偏下，避开脸；小标签放封面下方。
  cover_y=-7;cover_half=LINE_H*px['cover']/h*100;gap=(LINE_H*px['cover_label']/2+.012*h)/h*100
  # 接近方形（4:3、1:1）时左上角太窄，改为顶部居中。
  wide=w/h>=1.5
  lay.update(sub_y=-35,title_anchor='top-left' if wide else 'top-center',title_margin=(.045*w,.055*h),
             cover_y=cover_y,cover_label_y=round(cover_y-cover_half-gap,2),
             sub_chars=int(.68*w/px['sub']),title_chars=int((.40 if wide else .78)*w/px['title']),
             cover_chars=int(.59*w/px['cover']))
 return lay
def title_position(lay,lines,w,h,*,with_bar=False):
 """内容标题位置（FCPXML 单位：画面高度的百分比，原点在中心）。"""
 if lay['title_anchor']=='center':return lay['title_x'],lay['title_y']
 px=lay['px']['title'];left,top=lay['title_margin']
 if with_bar:top=max(top,lay['bar_h']+.012*h)
 box_w=max(text_px(s,px) for s in lines)+.72*px;box_h=len(lines)*LINE_H*px
 x=round((left+box_w/2-w/2)/h*100,2) if lay['title_anchor']=='top-left' else 0
 return x,round(50-(top+box_h/2)/h*100,2)
def png(path,w,h,rgba,*,divisions=1):
 def chunk(tag,data):return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
 if divisions==1:
  row=bytes(rgba)*w
 else:
  row=bytearray(w*4)
  line_width=max(1,round(h/30))
  for index in range(1,divisions):
   left=max(0,min(w-line_width,round(index*w/divisions)-line_width//2))
   row[left*4:(left+line_width)*4]=bytes(rgba)*line_width
 raw=(b'\0'+row)*h
 data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw,9))+chunk(b'IEND',b'')
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return path

def chapter_label_layout(text,cell_width,max_px,height):
    """Fit one editable label in its cell, leaving side bearings for legibility."""
    label = ' '.join(text.split())
    units = text_px(label, 1, ascii_width=.83)
    available_px = .84 * cell_width
    ideographs = all(
        '\u3400' <= ch <= '\u9fff' or '\U00020000' <= ch <= '\U000323af'
        for ch in label
    )
    if 2 <= len(label) <= 5 and ideographs:
        for max_length, space, em in ((3, '\u2004', 1/3), (4, '\u2005', 1/4), (5, '\u2009', 1/5)):
            spaced_units = units + em * (len(label) - 1)
            if len(label) <= max_length and spaced_units * max_px <= available_px:
                label = space.join(label)
                units = spaced_units
                break
    px = min(max_px, available_px / units)
    font_size = max(.1, math.floor(px * 10800 / height) / 10)
    return label, font_size, px < .75 * max_px

def title_options(copy, *, require_all=False):
    """Accept three two-line choices per title, or one per title from a legacy plan."""
    options = {}
    fingerprints = set()
    for key in ('content_title', 'cover_title'):
        value = copy.get(key)
        if isinstance(value, list) and len(value) == 2 and all(isinstance(line, str) for line in value):
            value = [value]
        elif not isinstance(value, list) or len(value) != 3:
            raise ValueError(f'{key} 需要三组两行文案；先通读最终内容再写')
        for lines in value:
            if not isinstance(lines, list) or len(lines) != 2 or not all(
                isinstance(line, str) and line.strip() and line.splitlines() == [line] and '\x00' not in line
                for line in lines
            ):
                raise ValueError(f'{key} 的每组文案需要两行非空文字')
            fingerprint = ''.join(''.join(lines).split())
            if fingerprint in fingerprints:
                raise ValueError('内容标题与封面标题的每组文案需要不同')
            fingerprints.add(fingerprint)
        options[key] = value
    counts = {len(choices) for choices in options.values()}
    if require_all and counts != {3}:
        raise ValueError('正式交付需要内容标题和封面标题各三组。保留原首组，按最终文稿补到三组后重试。')
    if len(counts) != 1:
        raise ValueError('内容标题与封面标题都需要三组选项；旧计划可各用一组')
    return options


def validate_copy(copy,known,total):
 options=title_options(copy)
 chapters=copy.get('chapters',[])
 if total>120 and not chapters:raise ValueError('超过两分钟需写章节名与起点；起点用保留段或原文编号')
 prev=-1
 for c in chapters:
  if not isinstance(c,dict) or c.get('start') not in known:raise ValueError('章节起点不在最终保留的内容中')
  label=c.get('title')
  if not isinstance(label,str) or not label.strip() or '\x00' in label:raise ValueError('章节需要可显示的名称')
  t=known[c['start']]
  if not 0<=t<total:raise ValueError('章节起点越界')
  if t<=prev:raise ValueError('章节起点需要按成片顺序递增')
  prev=t
 return options

def decorate(xml,clips,captions,copy,folder,width,height,frame):
 """Decorate clips with frame-aligned unit_offsets, when present, in timeline seconds."""
 width,height=int(width),int(height);frame=Fraction(frame);lay=layout_for(width,height)
 res=xml.find('resources');total=max((c['offset']+c['duration'] for c in clips),default=Fraction(0));known={};unit_offsets={}
 for c in clips:
  known.setdefault(c['id'].split('.')[0],c['offset'])
  for u in c.get('unit_ids',[]):known.setdefault(u,c['offset'])
  for u,offset in c.get('unit_offsets',{}).items():unit_offsets.setdefault(u,offset)
 known.update(unit_offsets)
 options=validate_copy(copy,known,total);notes=[];n=0
 if len(options['content_title'])==1:
  notes.append('旧计划每类标题只有一组。重交付前按最终文稿各补齐三组。')
 effect='cyTitle'
 ET.SubElement(res,'effect',id=effect,name='Basic Title',uid='.../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti')
 def host(t):return next(c for c in clips if c['offset']<=t<c['offset']+c['duration'])
 def title(t0,t1,text,name,lane,y,size,extra=None,x=0,*,enabled=True):
  nonlocal n
  # 起止都取最近帧：前一条的终点与后一条的起点相同时落在同一帧，不会叠 1 帧被导入器拆到两条轨上。
  t0=max(Fraction(0),round(Fraction(str(t0))/frame)*frame);t1=min(total,round(Fraction(str(t1))/frame)*frame)
  if t1<=t0:t1=min(total,t0+frame)
  if t1<=t0:return
  h=host(t0);n+=1
  el=ET.SubElement(h['node'],'title',ref=effect,lane=str(lane),name=name,offset=ts(h['start']+t0-h['offset']),start='0s',duration=ts(t1-t0))
  if not enabled:el.set('enabled','0')
  ET.SubElement(ET.SubElement(el,'text'),'text-style',ref=f'cyts{n}').text=text
  ET.SubElement(ET.SubElement(el,'text-style-def',id=f'cyts{n}'),'text-style',{'font':'Arial','fontSize':str(size),'fontColor':'1 1 1 1','alignment':'center',**(extra or {})})
  ET.SubElement(el,'adjust-transform',position=f'{_num(x)} {y}')
 for cap in captions:title(cap['start'],cap['end'],cap['text'],'字幕',1,lay['sub_y'],lay['sub_size'],SHADOW)
 for key,limit in (('content_title',lay['title_chars']),('cover_title',lay['cover_chars'])):
  for index,lines in enumerate(options[key],1):
   if any(len(line)>limit for line in lines):
    notes.append(f'{key} 第 {index} 组有一行超过 {limit} 字，需缩短或核对版式')
 for index,lines in enumerate(options['content_title'],1):
  tx,ty=title_position(lay,lines,width,height,with_bar=total>120)
  name='内容标题' if index==1 else f'内容标题（备选 {index}）'
  lane=2 if index==1 else index+6
  title(0,total,'\n'.join(lines),name,lane,ty,lay['title_size'],dict(SHADOW,bold='1',backgroundColor='0 0 0 0.55'),x=tx,enabled=index==1)
 for index,lines in enumerate(options['cover_title'],1):
  name='封面标题参考' if index==1 else f'封面标题参考（备选 {index}）'
  lane=6 if index==1 else index+8
  title(0,min(5,total),'\n'.join(lines),name,lane,lay['cover_y'],lay['cover_size'],dict(SHADOW,bold='1'),enabled=index==1)
 title(0,min(5,total),'封面标题参考','封面标题参考（可删除）',7,lay['cover_label_y'],lay['cover_label_size'],SHADOW)
 if total>120:
  chapters=copy['chapters']
  count=len(chapters)
  # 剪口本来就在成片帧网格上。转成 float 再向下取整会在分数帧率下取到上一剪口。
  begin=known[chapters[0]['start']]
  h=host(begin);bh=lay['bar_h']
  bar_y=round((50-bh/height*50)*(-1 if lay['vertical'] else 1),5)
  line_h=max(2,round(bh/8))
  line_y=round(bar_y-(bh-line_h)/height*50,5)
  label_y=round(bar_y+line_h/height*50,5)
  last_frame=total-frame
  span=Fraction(width*100,height)
  progress=[(known[chapter['start']],-span*(count-index)/count) for index,chapter in enumerate(chapters)]
  # Fill the current cell during its chapter, regardless of how long adjacent chapters last.
  # If the last chapter is just one frame, there is room only for the complete state.
  if progress[-1][0]==last_frame:progress[-1]=(last_frame,0)
  else:progress.append((last_frame,0))
  # 剪映可能把图片放在文字上层。填充线贴格底，越过章名时也不会盖暗文字。
  bounds=[('cyBarBG',(16,16,16,90),3,'进度条底',1,bh,bar_y),
          ('cyBarFill',(245,245,245,225),4,'进度条填充',1,line_h,line_y)]
  if count>1:
   bounds.append(('cyBarGrid',(255,255,255,100),5,'进度条格线',count,bh,bar_y))
  # Full-width colour rectangles slide from outside the canvas. Visible width increases
  # continuously; no crop or unsupported independent X scaling is necessary.
  for aid,rgba,lane,name,divisions,asset_height,y in bounds:
   path=png(Path(folder)/(aid+f'_{width}x{asset_height}_{divisions}.png'),width,asset_height,rgba,divisions=divisions)
   ET.SubElement(res,'format',id=aid+'Fmt',width=str(width),height=str(asset_height))
   asset=ET.SubElement(res,'asset',id=aid,name=name,start='0s',duration=ts(total),hasVideo='1',format=aid+'Fmt')
   if xml.get('version')=='1.8':asset.set('src',path.resolve().as_uri())
   else:ET.SubElement(asset,'media-rep',kind='original-media',src=path.resolve().as_uri())
   el=ET.SubElement(h['node'],'video',ref=aid,lane=str(lane),name=name,start='0s',offset=ts(h['start']+begin-h['offset']),duration=ts(total-begin))
   x=progress[0][1] if lane==4 else 0
   tr=ET.SubElement(el,'adjust-transform',position=f'{float(x):.5f} {y}')
   if lane==4 and len(progress)>1:
    anim=ET.SubElement(ET.SubElement(tr,'param',name='position'),'keyframeAnimation')
    for moment,x in progress:
     ET.SubElement(anim,'keyframe',time=ts(moment-begin),value=f'{float(x):.5f} {y}',interp='linear',curve='linear')
  cell_width=width/count
  max_px=min(lay['px']['label'],.94*(bh-line_h))
  for index,chapter in enumerate(chapters):
   label,size,small=chapter_label_layout(chapter['title'],cell_width,max_px,height)
   if small:
    notes.append(f"章节「{chapter['title']}」需要较小字号，核对后可缩短")
   x=((index+.5)*cell_width-width/2)/height*100
   title(begin,total,label,f'进度条章节 {index+1}',12+index,label_y,size,SHADOW,x=x)
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
