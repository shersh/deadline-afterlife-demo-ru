"""Private integration test; never commit the supplied game files."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def primary(env):
    return next(f for f in env.files.values() if hasattr(f, 'objects'))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--original',type=Path,required=True)
    parser.add_argument('--translated',type=Path,required=True)
    parser.add_argument('--managed',type=Path,required=True)
    parser.add_argument('--package',type=Path,default=ROOT)
    args=parser.parse_args()
    import UnityPy
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    fixture=ROOT/'.test-work'/('integration-'+stamp)
    fixture.mkdir(parents=True)
    manifest=json.loads((ROOT/'payload/manifest.json').read_text(encoding='utf8'))
    for entry in manifest['files']:
        source=args.original/entry['path']
        assert sha(source)==entry['sha256'],entry['path']
        target=fixture/entry['path'];target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    for source in args.managed.glob('*.dll'):
        if source.name=='Assembly-CSharp.dll':continue
        shutil.copy2(source,fixture/'DEADLINE_Data/Managed'/source.name)
    (fixture/'DEADLINE.exe').write_text('Integration fixture only. Not a game executable.\n',encoding='ascii')
    (fixture/'example-player-save.keep').write_bytes(b'USER SAVE MUST REMAIN UNCHANGED')
    python=args.package/'runtime/python.exe'
    def run(action,success=True):
        result=subprocess.run([str(python),'-I','-X','utf8',str(args.package/'patch.py'),action,'--game',str(fixture)],capture_output=True,text=True,encoding='utf8',errors='replace')
        if (result.returncode==0)!=success:
            raise AssertionError(action+' unexpected exit: '+result.stdout+'\n'+result.stderr)
        return result
    run('check')
    install=run('install')
    verified=0
    for entry in manifest['files']:
        actual=fixture/entry['path']
        reference=args.translated/entry['path']
        if actual.suffix=='.assets' or actual.name=='level0':
            # Read bytes first so UnityPy cannot keep Windows file handles open
            # during the subsequent installer restore test.
            a=primary(UnityPy.load(actual.read_bytes()));b=primary(UnityPy.load(reference.read_bytes()))
            assert set(a.objects)==set(b.objects),entry['path']
            for object_id,obj in a.objects.items():
                assert obj.get_raw_data()==b.objects[object_id].get_raw_data(),(entry['path'],object_id)
                verified+=1
        else:
            assert actual.read_bytes()==reference.read_bytes(),entry['path']
    installed_hashes={e['path']:sha(fixture/e['path']) for e in manifest['files']}
    run('install')
    assert installed_hashes=={e['path']:sha(fixture/e['path']) for e in manifest['files']}
    run('restore')
    for entry in manifest['files']:
        assert sha(fixture/entry['path'])==entry['sha256'],('restore',entry['path'])
    bad=fixture/manifest['files'][0]['path']
    bad.write_bytes(bad.read_bytes()+b'unsupported update')
    before={e['path']:sha(fixture/e['path']) for e in manifest['files']}
    run('install',success=False)
    assert before=={e['path']:sha(fixture/e['path']) for e in manifest['files']}
    assert (fixture/'example-player-save.keep').read_bytes()==b'USER SAVE MUST REMAIN UNCHANGED'
    summary={'patch_version':manifest['patch_version'],'portable_runtime':True,'integration_install':'passed','unity_objects_compared':verified,'assembly_and_csv_byte_comparison':'passed','repeat_install':'no changes','restore':'all original SHA-256 restored','unsupported_version':'refused without modifying files','player_save':'unchanged'}
    (ROOT/'tests/VALIDATION.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(install.stdout.strip())
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
