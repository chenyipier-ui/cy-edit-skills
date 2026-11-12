import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('updater', ROOT/'skills/cy-edit-shortvideo/scripts/cy_auto_update.py')
u=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(u)
NAME='cy-edit-shortvideo'

class Updates(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
  self.base=Path(self.temp.name).resolve(); self.root=self.base/NAME; self.root.mkdir(); (self.root/'VERSION').write_text('2.4.0')
  self.env=patch.dict(os.environ,{'CY_EDIT_UPDATE_DIR':str(self.base/'cache'),'CY_EDIT_AUTO_UPDATE':'1'}); self.env.start();self.addCleanup(self.env.stop)
  self.filename=NAME+'-v2.4.1.zip'
  stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w') as z:
   for path,content in {'VERSION':'2.4.1','SKILL.md':'new instructions','LICENSE':'CY','scripts/editor.py':'print("new runtime")','scripts/cy_auto_update.py':'# next updater'}.items(): z.writestr(NAME+'/'+path,content)
  self.data=stream.getvalue(); self.checksum=hashlib.sha256(self.data).hexdigest()
 def get(self,url):
  if '/releases?' in url: return json.dumps([{'tag_name':'v2.4.1','assets':[{'name':self.filename},{'name':'SHA256SUMS'}]}]).encode()
  if url.endswith('SHA256SUMS'): return (self.checksum+'  '+self.filename+'\n').encode()
  return self.data
 def test_verified_upgrade_and_offline_reuse(self):
  with patch.object(u,'get',side_effect=self.get): new=u.resolve(self.root,True)
  self.assertEqual((new/'VERSION').read_text(),'2.4.1')
  self.assertEqual((self.root/'VERSION').read_text(),'2.4.0')
  with patch.object(u,'get',side_effect=OSError('offline')): self.assertEqual(u.resolve(self.root,True),new)
 def test_corruption_never_activates(self):
  self.checksum='0'*64
  with patch.object(u,'get',side_effect=self.get): self.assertEqual(u.resolve(self.root,True),self.root)
  self.assertEqual(list((self.base/'cache').glob('**/.ready')),[])
 def test_traversal_rejected_before_extraction(self):
  stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w') as z:z.writestr(NAME+'/../../escape','bad')
  with self.assertRaises(ValueError):u.unpack(stream.getvalue(),hashlib.sha256(stream.getvalue()).hexdigest(),self.base,NAME,'v2.4.1')
  self.assertFalse((self.base/'escape').exists())
 def test_disabled_never_networks(self):
  with patch.dict(os.environ,{'CY_EDIT_AUTO_UPDATE':'0'}),patch.object(u,'get') as get:
   self.assertEqual(u.resolve(self.root,True),self.root);get.assert_not_called()
 def test_concurrent_check_uses_existing(self):
  lock=self.base/'cache'/NAME/'.lock';lock.mkdir(parents=True)
  with patch.object(u,'get') as get:
   self.assertEqual(u.resolve(self.root,True),self.root);get.assert_not_called()
  self.assertTrue(lock.exists())
 def test_github_fallback(self):
  def fetch(url):
   if 'gitee.com' in url:raise OSError('unavailable')
   return self.get(url)
  with patch.object(u,'get',side_effect=fetch):self.assertNotEqual(u.resolve(self.root,True),self.root)
 def test_both_packages_ship_identical_updater(self):
  self.assertEqual((ROOT/'skills/cy-edit-shortvideo/scripts/cy_auto_update.py').read_bytes(),(ROOT/'skills/cy-edit-longvideo/scripts/cy_auto_update.py').read_bytes())

if __name__=='__main__':unittest.main()
