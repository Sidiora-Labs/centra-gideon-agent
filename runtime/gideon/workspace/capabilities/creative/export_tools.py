"""Native approval-aware manuscript export tools."""
import json
from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel,ToolDefinition,ToolProvider,ToolResult
from .exports import ExportError,ManuscriptExports


class ExportTools(ToolProvider):
    def __init__(self,store=None):self.store=store or ManuscriptExports(config_dir())
    @property
    def name(self):return 'gideon-creative-exports'
    @property
    def display_name(self):return 'EPUB and print manuscript exports'
    async def list_tools(self):
        return [
            ToolDefinition(name='creative_manuscript_exports',provider=self.name,description='List pinned manuscript export receipts and authenticated download paths.',parameters={'type':'object','properties':{},'additionalProperties':False},requires_approval=False,risk_level=RiskLevel.SAFE),
            ToolDefinition(name='creative_manuscript_export',provider=self.name,description='Render pinned canonical manuscript versions to durable EPUB and print PDF artifacts.',parameters={'type':'object','properties':{key:{'type':'integer' if key=='source_revision' else 'string'} for key in ('request_id','source_kind','source_id','source_revision','title','creator','language','identifier')},'required':['request_id','source_kind','source_id','source_revision','title','creator','language','identifier'],'additionalProperties':False},requires_approval=True,risk_level=RiskLevel.CAUTION),
        ]
    async def invoke(self,name,arguments):
        try:
            if name=='creative_manuscript_exports' and arguments=={}:result={'items':self.store.list()}
            elif name=='creative_manuscript_export' and isinstance(arguments,dict):result=self.store.create(arguments)
            else:raise ExportError('Invalid manuscript export tool arguments')
            return ToolResult(success=True,output=json.dumps(result))
        except ExportError as error:return ToolResult(success=False,error=str(error),metadata={'status':error.status,'code':error.code})


def create_provider(**kwargs):return ExportTools()
