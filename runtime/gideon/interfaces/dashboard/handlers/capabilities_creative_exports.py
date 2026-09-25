"""Owner HTTP surface for pinned manuscript export artifacts."""
from aiohttp import web
from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.creative.exports import ExportError, ManuscriptExports
from gideon.workspace.capabilities.creative.store import CatalogError

PREFIX='/api/capabilities/creative/exports'


def _owner(request):
    if not request.get('user') or request.get('app'):raise web.HTTPForbidden(text='Dashboard authentication required')


def _store(request):
    factory=request.app.get('creative_exports_factory');return factory() if factory else ManuscriptExports(config_dir())


async def collection(request):
    _owner(request)
    try:
        result=_store(request).create(await read_json_body(request)) if request.method=='POST' else {'items':_store(request).list()}
        return web.json_response(result,status=201 if request.method=='POST' else 200,headers={'Cache-Control':'no-store'})
    except CatalogError as error:return web.json_response({'error':str(error),'code':getattr(error,'code','source_error')},status=error.status)


async def item(request):
    _owner(request)
    try:return web.json_response(_store(request).get(request.match_info['id']),headers={'Cache-Control':'no-store'})
    except ExportError as error:return web.json_response({'error':str(error),'code':error.code},status=error.status)


async def download(request):
    _owner(request)
    try:
        path,mime=_store(request).file(request.match_info['id'],request.match_info['format'])
        return web.FileResponse(path,headers={'Content-Type':mime,'Content-Disposition':f'attachment; filename="{path.name}"','Cache-Control':'private, no-store'})
    except ExportError as error:return web.json_response({'error':str(error),'code':error.code},status=error.status)


def register(app):
    app.router.add_get(PREFIX,collection,name='creative-exports-list');app.router.add_post(PREFIX,collection,name='creative-exports-create')
    app.router.add_get(PREFIX+'/{id}',item,name='creative-exports-get');app.router.add_get(PREFIX+'/{id}/{format:epub|print}',download,name='creative-exports-download')
