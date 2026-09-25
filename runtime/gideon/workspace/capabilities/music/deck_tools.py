"""Native card deck and canonical print operations."""
import json
from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel,ToolDefinition,ToolProvider,ToolResult
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.sketches import SketchError
from .decks import DeckStore
from .store import DomainError


class DeckTools(ToolProvider):
    def __init__(self,store=None):
        home=config_dir();self.store=store or DeckStore(home,NativeArtifactProvider(root=home/'artifacts'))

    @property
    def name(self):return 'gideon-music-decks'

    @property
    def display_name(self):return 'Card decks'

    async def list_tools(self):
        result=[]
        for action in ('list','get','create','update','card','history','export','generate','adopt'):
            keys=[] if action=='list' else ['data'] if action=='create' else ['id'] if action in ('get','history') else ['id','key','data'] if action in ('card','adopt') else ['id','data']
            readonly=action in ('list','get','history')
            result.append(ToolDefinition(name='music_decks_'+action,provider=self.name,description=action+' a persisted card deck.',parameters={'type':'object','properties':{key:{'type':'object' if key=='data' else 'string'} for key in keys},'required':keys,'additionalProperties':False},requires_approval=not readonly,risk_level=RiskLevel.SAFE if readonly else RiskLevel.CAUTION))
        return result

    async def invoke(self,tool_name,arguments):
        definitions={tool.name:tool.parameters for tool in await self.list_tools()}
        if tool_name not in definitions or not isinstance(arguments,dict) or set(arguments)!=set(definitions[tool_name]['required']):return ToolResult(success=False,error='Invalid deck arguments')
        try:
            value=getattr(self.store,tool_name.removeprefix('music_decks_'))(*[arguments[key] for key in ('id','key','data') if key in arguments])
            return ToolResult(success=True,output=json.dumps(value))
        except (DomainError,SketchError,ValueError,TypeError) as exc:return ToolResult(success=False,error=str(exc),metadata={'status':getattr(exc,'status',400)})


def create_provider(**kwargs):return DeckTools()
