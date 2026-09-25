"""Native consumers of explicit image reconstruction jobs."""
import inspect
import json
from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel,ToolDefinition,ToolProvider,ToolResult
from gideon.workspace.artifacts.native import NativeArtifactProvider
from .image3d import Image3DStore
from .store import DomainError


class Image3DTools(ToolProvider):
    def __init__(self,store=None):
        home=config_dir();self.store=store or Image3DStore(home,NativeArtifactProvider(root=home/'artifacts'))

    @property
    def name(self):
        return 'gideon-music-models3d'

    @property
    def display_name(self):
        return 'Image to 3D'

    async def list_tools(self):
        tools=[]
        for action in ('readiness','config','configure','list','get','submit','refresh','stop'):
            props={}
            if action in ('get','refresh','stop'):
                props['id']={'type':'string'}
            if action in ('configure','submit'):
                props['data']={'type':'object','description':'Config: enabled,credential_name,model,revision. Submit: request_id,title,image_ref{slug,version},target_polycount,should_texture,license.'}
            readonly=action in ('readiness','config','list','get')
            tools.append(ToolDefinition(name='music_models3d_'+action,provider=self.name,description=action.capitalize()+' explicit image-to-3D jobs.',parameters={'type':'object','properties':props,'required':list(props),'additionalProperties':False},requires_approval=not readonly,risk_level=RiskLevel.SAFE if readonly else RiskLevel.CAUTION))
        return tools

    async def invoke(self,tool_name,arguments):
        definitions={tool.name:tool.parameters for tool in await self.list_tools()}
        if tool_name not in definitions or not isinstance(arguments,dict) or set(arguments)!=set(definitions[tool_name]['required']):
            return ToolResult(success=False,error='Invalid image-to-3D arguments')
        try:
            result=getattr(self.store,tool_name.removeprefix('music_models3d_'))(*[arguments[key] for key in ('id','data') if key in arguments])
            if inspect.isawaitable(result):
                result=await result
            return ToolResult(success=True,output=json.dumps(result))
        except (DomainError,ValueError,TypeError) as exc:
            return ToolResult(success=False,error=str(exc),metadata={'code':getattr(exc,'code','invalid_input'),'status':getattr(exc,'status',400)})


def create_provider(**kwargs):
    return Image3DTools()
