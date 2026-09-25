"""Configured CLI launches and canonical image artifacts for owner terminals."""
import io
import os
import re
import shutil
from pathlib import Path
from PIL import Image
from gideon.core.config.loader import config_dir
from gideon.integrations.llm.registry import get_default_registry
from gideon.workspace.artifacts.registry import get_provider


def profiles():
    result=[]
    for entry in get_default_registry().list_entries():
        if entry.type!='acp_agent':continue
        engine=entry.options.get('requires_executable',{})
        path=engine.get('path','') if isinstance(engine,dict) else ''
        supported=entry.options.get('dialect')=='codex'
        available=supported and isinstance(path,str) and Path(path).is_absolute() and os.path.isfile(path) and os.access(path,os.X_OK)
        result.append({'id':entry.name,'available':bool(available),'image_supported':bool(available),'reason':None if available else 'Configured Codex engine executable required'})
    return result


def validate_image(data):
    if not isinstance(data,bytes) or not 0<len(data)<=8388608:raise ValueError('Image must be at most 8 MiB')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('PNG','JPEG','WEBP') or image.width*image.height>16000000:
                raise ValueError('Unsupported image format or dimensions')
            mime=Image.MIME[image.format]
            image.verify()
    except (OSError,Image.DecompressionBombError) as error:
        raise ValueError('Invalid image bytes') from error
    return mime


def upload(data,actor):
    mime=validate_image(data)
    artifact=get_provider().create_binary(name='Provider terminal image',data=data,mime=mime,source='workspace-provider-terminal',actor=actor)
    return {'artifact_id':artifact.slug,'version':artifact.version,'mime':mime,'bytes':len(data)}


def prepare(session_id,provider_id,image=None):
    if not re.fullmatch(r'[a-f0-9]{12}',session_id):raise ValueError('Invalid terminal identity')
    profile=next((item for item in profiles() if item['id']==provider_id),None)
    if not profile or not profile['available']:raise ValueError('Configured provider terminal unavailable')
    entry=get_default_registry().get_entry(provider_id)
    argv=[str(Path(entry.options['requires_executable']['path']).resolve())]
    if image is not None:
        if not isinstance(image,dict) or set(image)!={'artifact_id','version'} or not isinstance(image['artifact_id'],str) or type(image['version']) is not int or image['version']<1:
            raise ValueError('Image requires artifact identity and version')
        provider=get_provider()
        artifact=provider.get(image['artifact_id'],version=image['version'])
        if artifact is None or artifact.kind!='image':raise FileNotFoundError('Image artifact not found')
        raw=provider.raw_bytes(image['artifact_id'],version=image['version'])
        if raw is None:raise FileNotFoundError('Image artifact bytes unavailable')
        mime=validate_image(raw[0])
        folder=config_dir()/'capabilities/workspace/terminal-images'/session_id
        if not folder.resolve().is_relative_to(config_dir().resolve()):raise ValueError('Image cache escapes runtime home')
        folder.mkdir(parents=True,mode=0o700)
        path=folder/('image.'+{'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[mime])
        with path.open('xb') as output:output.write(raw[0])
        path.chmod(0o600)
        argv.extend(['--image',str(path)])
    return argv


def cleanup(session_id):
    if re.fullmatch(r'[a-f0-9]{12}',session_id):
        shutil.rmtree(config_dir()/'capabilities/workspace/terminal-images'/session_id,ignore_errors=True)
