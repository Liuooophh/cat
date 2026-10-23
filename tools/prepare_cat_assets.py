"""Rebuild transparent cat assets from the user's four unchanged MP4s.
Requires numpy, Pillow, scipy, OpenCV. Default tool installs live next to this file.
Sources are keyed with border-connected white-background classification, a
narrow edge color model, and decontamination before premultiplied resizing.
"""
from pathlib import Path
import argparse,json,subprocess,sys,shutil
import numpy as np
from PIL import Image,ImageDraw,ImageFont
sys.path.insert(0,str(Path(__file__).resolve().parent))
from scipy import ndimage as ndi
import cv2

ROOT=Path(__file__).resolve().parents[1]
try:
 import imageio_ffmpeg
 DEFAULT_FFMPEG=imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
 DEFAULT_FFMPEG=shutil.which('ffmpeg') or 'ffmpeg'
SPECS={
 'idle':{'source':'cat-idle-v1.mp4','first':117,'last':178,'pingpong':True,'loop':True,'ground_source_y':795},
 'walk':{'source':'cat-walk-v3.mp4','first':155,'last':179,'pingpong':False,'loop':True,'ground_source_y':760},
 'groom':{'source':'cat-groom-v1.mp4','first':0,'last':219,'pingpong':False,'loop':False,'ground_source_y':786},
 'sleep':{'source':'cat-sleep-v3.mp4','first':107,'last':178,'pingpong':False,'loop':True,'ground_source_y':654},
}
CELL=(320,300);GROUND=282;SCALE=.32

def remove_white(rgb):
 c=rgb.astype('float32')
 # White internal body patches remain opaque unless they are essentially the
 # same neutral white as the surrounding studio background.
 candidate=((rgb.min(2)>=232)&(np.ptp(rgb.astype('int16'),axis=2)<=12)).astype('uint8')
 n,labels,stats,_=cv2.connectedComponentsWithStats(candidate,8)
 border_ids=np.unique(np.concatenate([labels[0],labels[-1],labels[:,0],labels[:,-1]]))
 bg_ids=set(int(x) for x in border_ids if x)
 for label in range(1,n):
  if label not in bg_ids and stats[label,4]>=30:
   region=labels==label
   if float(c[region].mean())>=245.5:bg_ids.add(label)
 background=np.isin(labels,list(bg_ids))
 foreground=~background
 # Discard compression specks not attached to the cat silhouette.
 n,l,st,_=cv2.connectedComponentsWithStats(foreground.astype('uint8'),8)
 if n>1:foreground=l==(1+int(st[1:,4].argmax()))
 # A 4px inside / 5px outside trimap is only 1-2px at delivered resolution.
 inner=ndi.binary_erosion(foreground,iterations=4,border_value=0)
 outer=ndi.binary_dilation(foreground,iterations=5)
 band=outer&~inner
 alpha=foreground.astype('float32')
 # Estimate local background from clean nearby whites and local opaque fur
 # from the nearest reliable interior pixel.
 _,fg_indices=ndi.distance_transform_edt(~inner,return_indices=True)
 f=c[tuple(fg_indices)]
 clean_bg=~ndi.binary_dilation(foreground,iterations=8)
 _,bg_indices=ndi.distance_transform_edt(~clean_bg,return_indices=True)
 b=c[tuple(bg_indices)]
 b=np.clip(b,247,253)
 v=b-f;delta=b-c
 est=np.sum(delta*v,axis=2)/np.maximum(np.sum(v*v,axis=2),64)
 alpha[band]=np.clip(est[band],0,1)
 # Remove values too faint to survive 3x downsampling and desktop keying.
 alpha[alpha<.018]=0
 out=c.copy()
 visible=band&(alpha>.018)
 out[visible]=(c[visible]-(1-alpha[visible,None])*b[visible])/alpha[visible,None]
 out=np.clip(out,0,255)
 out[alpha==0]=0
 return out,alpha

def transform(rgb,alpha,spec):
 # Premultiplied area resampling keeps fine fur stable at 0.32x and prevents
 # transparent studio whites from bleeding into the silhouette.
 scaled=(round(1104*SCALE),round(832*SCALE))
 a=cv2.resize(alpha,scaled,interpolation=cv2.INTER_AREA)
 premul=cv2.resize(rgb*alpha[...,None],scaled,interpolation=cv2.INTER_AREA)
 color=np.divide(premul,a[...,None],out=np.zeros_like(premul),where=a[...,None]>.0001)
 im=Image.fromarray(np.dstack([np.clip(color,0,255).astype('uint8'),np.round(a*255).astype('uint8')]),'RGBA')
 canvas=Image.new('RGBA',CELL,(0,0,0,0))
 tx=round(CELL[0]/2-scaled[0]/2);ty=round(GROUND-spec['ground_source_y']*SCALE)
 canvas.paste(im,(tx,ty))
 return canvas

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--input-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,default=ROOT/'assets/cat');parser.add_argument('--ffmpeg',type=Path,default=DEFAULT_FFMPEG);parser.add_argument('--only',choices=list(SPECS));parser.add_argument('--qa-dir',type=Path,default=ROOT/'work/cat-preview');args=parser.parse_args()
 args.output_dir.mkdir(exist_ok=True,parents=True)
 manifest={'format_version':1,'cell_size':list(CELL),'ground_y':GROUND,'source_size':[1104,832],'source_scale':SCALE,'fps':24,'clips':{}}
 for state,spec in SPECS.items():
  count=(spec['last']-spec['first']+1)*(2 if spec['pingpong'] else 1)-(2 if spec['pingpong'] else 0)
  manifest['clips'][state]={'fps':24,'loop':spec['loop'],'source_facing':'left','frame_count':count,'source_file':spec['source'],'source_start_frame':spec['first'],'source_end_frame':spec['last'],'pingpong':spec['pingpong'],'ground_source_y':spec['ground_source_y']}
  if args.only and args.only!=state:continue
  target=args.output_dir/state;target.mkdir(exist_ok=True)
  # Delete only generated numbered frames inside this exact output state.
  for old in target.glob('[0-9][0-9][0-9][0-9].png'):old.unlink()
  cmd=[str(args.ffmpeg),'-v','error','-i',str(args.input_dir/spec['source']),'-vf',f"select=between(n\\,{spec['first']}\\,{spec['last']})",'-fps_mode','passthrough','-f','rawvideo','-pix_fmt','rgb24','pipe:1']
  p=subprocess.Popen(cmd,stdout=subprocess.PIPE);frames=[]
  while True:
   raw=p.stdout.read(1104*832*3)
   if len(raw)!=1104*832*3:break
   rgb=np.frombuffer(raw,dtype='uint8').reshape(832,1104,3)
   color,alpha=remove_white(rgb)
   frames.append(transform(color,alpha,spec))
   if len(frames)%24==0:print(state,len(frames),flush=True)
  if p.wait()!=0:raise RuntimeError(f'ffmpeg failed for {state}')
  if len(frames)!=spec['last']-spec['first']+1:raise RuntimeError(f'Unexpected frame count for {state}: {len(frames)}')
  if spec['pingpong']:frames+=frames[-2:0:-1]
  for i,im in enumerate(frames,1):im.save(target/f'{i:04d}.png',compress_level=6)
  print('SAVED',state,len(frames),flush=True)
 (args.output_dir/'clips.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
 args.qa_dir.mkdir(parents=True,exist_ok=True)
 build_qa(args.output_dir,args.qa_dir,str(args.ffmpeg))

def build_qa(assets,qa,ffmpeg):
 font=ImageFont.truetype(r'C:\Windows\Fonts\arial.ttf',16)
 states=list(SPECS);sheet=Image.new('RGB',(4*320,4*330),(230,232,236));draw=ImageDraw.Draw(sheet)
 for row,state in enumerate(states):
  files=sorted((assets/state).glob('*.png'))
  if not files:continue
  for col,(frac,bg) in enumerate([(0,(245,245,245)),(.34,(27,32,41)),(.67,(86,74,109)),(1,(180,204,215))]):
   frame=Image.open(files[min(len(files)-1,int((len(files)-1)*frac))]).convert('RGBA')
   panel=Image.new('RGBA',CELL,bg+(255,));panel.alpha_composite(frame)
   sheet.paste(panel.convert('RGB'),(col*320,row*330+30));draw.text((col*320+10,row*330+6),f'{state} | f{int((len(files)-1)*frac)+1} / {len(files)}',font=font,fill=(20,24,32))
 sheet.save(qa/'qa-contact-sheet.jpg',quality=96)
 # 4 simultaneous synchronized poses over dark/light split background.
 allframes={s:[Image.open(p).convert('RGBA') for p in sorted((assets/s).glob('*.png'))] for s in states}
 if any(not f for f in allframes.values()):return
 rawpipe=subprocess.Popen([ffmpeg,'-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','1280x330','-r','24','-i','pipe:0','-an','-c:v','libx264','-pix_fmt','yuv420p','-crf','18',str(qa/'cat-animation-preview.mp4')],stdin=subprocess.PIPE)
 for idx in range(240):
  panel=Image.new('RGB',(1280,330),(26,31,40));d=ImageDraw.Draw(panel)
  for k,state in enumerate(states):
   frames=allframes[state];frame=frames[idx%len(frames)] if SPECS[state]['loop'] else frames[min(idx,len(frames)-1)]
   bg=Image.new('RGBA',CELL,(26,31,40,255));bd=ImageDraw.Draw(bg);bd.rectangle((160,0,320,300),fill=(235,239,243,255));bg.alpha_composite(frame)
   panel.paste(bg.convert('RGB'),(k*320,30));d.text((k*320+12,7),state,font=font,fill='white')
  rawpipe.stdin.write(panel.tobytes())
 rawpipe.stdin.close();rawpipe.wait()
 print('QA',qa/'qa-contact-sheet.jpg',qa/'cat-animation-preview.mp4',flush=True)

if __name__=='__main__':main()

