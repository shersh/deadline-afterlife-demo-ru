"""Build a redistributable patch from private original/translated game files.

Development only: UnityPy 1.25.4 is needed here, not by the installer.
No original assets, font binaries, or decompiled game sources are exported.
"""
import argparse
import base64
import csv
import difflib
import hashlib
import json
import struct
from pathlib import Path

def sha(data):
    return hashlib.sha256(data).hexdigest()

def primary(env):
    return next(f for f in env.files.values() if hasattr(f, 'objects'))

def encode(data):
    return base64.b64encode(data).decode('ascii')

def byte_edits(before, after):
    if before == after:
        return []
    prefix = 0
    while prefix < min(len(before),len(after)) and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while suffix < min(len(before),len(after))-prefix and before[-1-suffix] == after[-1-suffix]:
        suffix += 1
    a = before[prefix:len(before)-suffix if suffix else None]
    b = after[prefix:len(after)-suffix if suffix else None]
    edits=[]
    for op,i,j,k,l in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
        if op != 'equal':
            edits.append([prefix+i,j-i,encode(b[k:l])])
    return edits

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('original',type=Path)
    parser.add_argument('translated',type=Path)
    parser.add_argument('code_translations',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    import UnityPy
    output=args.output
    output.mkdir(parents=True,exist_ok=True)
    files=[]
    csv_patches=[]
    for translated in sorted((args.translated/'DEADLINE_Data/StreamingAssets/data').glob('*.csv')):
        relative=translated.relative_to(args.translated).as_posix()
        original=args.original/relative
        old=list(csv.reader(original.open(encoding='utf-8-sig',newline='')))
        new=list(csv.reader(translated.open(encoding='utf-8-sig',newline='')))
        changes=[]
        assert old[0] == new[0]
        for before,after in zip(old[1:],new[1:]):
            assert before[0]==after[0] and len(before)==len(after)
            for i,(a,b) in enumerate(zip(before,after)):
                if a!=b:
                    changes.append({'id':before[0],'column':old[0][i],'value':b})
        csv_patches.append({'path':relative,'changes':changes,'append':new[len(old):]})
        files.append({'path':relative,'sha256':sha(original.read_bytes()),'legacy_translated_sha256':sha(translated.read_bytes())})
    font_files={('resources.assets',119):'consola.ttf',('sharedassets0.assets',1277):'cour.ttf',('sharedassets0.assets',1278):'courbd.ttf',('sharedassets0.assets',1279):'arialbd.ttf'}
    asset_patches=[]
    for name in ['resources.assets','sharedassets0.assets','level0']:
        relative='DEADLINE_Data/'+name
        original=args.original/relative
        translated=args.translated/relative
        source=original.read_bytes()
        old=primary(UnityPy.load(str(original)))
        new=primary(UnityPy.load(str(translated)))
        assert old.header.version==22 and old.header.endian=='<'
        table=[]
        changes=[]
        for obj in sorted(old.objects.values(),key=lambda o:o.byte_start):
            pattern=struct.pack('<qqIi',obj.path_id,obj.byte_start-old.header.data_offset,obj.byte_size,obj.type_id)
            metadata=source[:old.header.data_offset]
            assert metadata.count(pattern)==1,(name,obj.path_id,'metadata entry')
            at=metadata.index(pattern)
            table.append([obj.path_id,obj.byte_start,obj.byte_size,at+8])
            before=obj.get_raw_data()
            after=new.objects[obj.path_id].get_raw_data()
            if before==after:
                continue
            font=font_files.get((name,obj.path_id))
            if font:
                tree=new.objects[obj.path_id].read_typetree()
                blob=bytes(tree['m_FontData'])
                pos=after.index(blob)
                assert after.count(blob)==1 and struct.unpack_from('<I',after,pos-4)[0]==len(blob)
                end=(pos+len(blob)+3)//4*4
                changes.append({'id':obj.path_id,'font':font,'prefix':encode(after[:pos-4]),'suffix':encode(after[end:])})
            else:
                changes.append({'id':obj.path_id,'edits':byte_edits(before,after),'output_sha256':sha(after)})
        asset_patches.append({'path':relative,'data_offset':old.header.data_offset,'objects':table,'changes':changes})
        files.append({'path':relative,'sha256':sha(source),'legacy_translated_sha256':sha(translated.read_bytes())})
    code=[]
    for r in json.loads(args.code_translations.read_text(encoding='utf8')):
        code.append({'type':r['type'],'method':r['method'],'source_hash':sha(r['source'].encode('utf8')),'target':r['target']})
    relative='DEADLINE_Data/Managed/Assembly-CSharp.dll'
    files.append({'path':relative,'sha256':sha((args.original/relative).read_bytes()),'legacy_translated_sha256':sha((args.translated/relative).read_bytes())})
    manifest={'patch_version':'1.0.0','game':'DEADLINE: Afterlife Processing Demo','unity_version':'6000.3.5f2','files':files}
    for name,data in [('manifest.json',manifest),('csv.json',csv_patches),('assets.json',asset_patches),('assembly.json',code)]:
        (output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('Exported:',len(files),'files;',sum(len(p['changes']) for p in csv_patches),'CSV cells;',sum(len(p['changes']) for p in asset_patches),'asset objects;',len(code),'assembly translations')

if __name__=='__main__':
    main()
