"""Spotify authorization-code PKCE using the canonical private credential store."""
import asyncio
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode,urlsplit
import aiohttp
from .image3d import response_json
from .store import DomainError,text

SCOPES='user-read-recently-played playlist-read-private playlist-read-collaborative'
TOKEN_URL='https://accounts.spotify.com/api/token'
_LOCKS={}


class SpotifyOAuth:
    def __init__(self,bridge):
        self.bridge=bridge
        self.credentials=bridge.credentials

    def _local(self):
        if self.credentials is None:raise DomainError('Authorization belongs to the configured connection service',409,'connection_owned')

    def begin(self,data):
        self._local()
        if not isinstance(data,dict) or set(data)!={'client_id','redirect_uri'}:raise DomainError('client_id and redirect_uri required')
        client=text(data['client_id'],'client ID',200,True);redirect=text(data['redirect_uri'],'redirect URI',2000,True)
        parsed=urlsplit(redirect)
        if parsed.fragment or parsed.username or not parsed.netloc or not (parsed.scheme=='https' or parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','::1')):raise DomainError('HTTPS or explicit loopback redirect URI required')
        config=self.bridge.config()
        if not config['credential_name']:raise DomainError('Save a named Spotify connection first')
        state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        pending={'client_id':client,'redirect_uri':redirect,'verifier':verifier,'expires_at':time.time()+600,'credential_name':config['credential_name']}
        self.credentials.put('spotify-pkce-'+state,{'type':'oauth2','value':json.dumps(pending)})
        return {'state':state,'expires_in':600,'authorization_url':'https://accounts.spotify.com/authorize?'+urlencode({'response_type':'code','client_id':client,'scope':SCOPES,'redirect_uri':redirect,'state':state,'code_challenge_method':'S256','code_challenge':challenge})}

    def pending(self,state):
        self._local();text(state,'state',100,True);self.credentials.reload()
        try:pending=json.loads(self.credentials.resolve('spotify-pkce-'+state).secret or '')
        except (KeyError,ValueError):raise DomainError('Unknown or consumed OAuth state',409,'oauth_state')
        if pending['expires_at']<time.time():
            self.credentials.remove('spotify-pkce-'+state)
            raise DomainError('OAuth state expired',409,'oauth_state')
        return pending

    async def _token(self,fields):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(TOKEN_URL,data=fields,allow_redirects=False) as response:
                if response.status!=200:raise DomainError('Spotify token exchange returned HTTP '+str(response.status),502,'oauth_exchange')
                result=await response_json(response)
        if not isinstance(result.get('access_token'),str) or not result['access_token'] or type(result.get('expires_in')) is not int or result['expires_in']<=0:raise DomainError('Invalid Spotify token response',502,'oauth_exchange')
        return result

    async def complete(self,data):
        async with _LOCKS.setdefault(str(self.bridge.path.resolve()),asyncio.Lock()):return await self._complete(data)

    async def _complete(self,data):
        if not isinstance(data,dict) or set(data)!={'state','code'}:raise DomainError('state and authorization code required')
        text(data['code'],'authorization code',4000,True)
        pending=self.pending(data['state'])
        self.credentials.remove('spotify-pkce-'+data['state'])
        token=await self._token({'grant_type':'authorization_code','code':data['code'],'redirect_uri':pending['redirect_uri'],'client_id':pending['client_id'],'code_verifier':pending['verifier']})
        self._save(pending['credential_name'],pending['client_id'],token)
        return {'connected':True,'credential_name':pending['credential_name'],'expires_in':token['expires_in']}

    def _save(self,name,client,token,refresh=None):
        value={'access_token':token['access_token'],'refresh_token':token.get('refresh_token') or refresh,'client_id':client,'expires_at':time.time()+token['expires_in']}
        self.credentials.put(name,{'type':'oauth2','value':json.dumps(value)})

    async def access(self,name):
        self._local();self.credentials.reload()
        try:secret=self.credentials.resolve(name).secret
        except KeyError:return None
        try:token=json.loads(secret or '')
        except ValueError:return secret
        if not isinstance(token,dict) or 'access_token' not in token:return secret
        if token['expires_at']>time.time()+30:return token['access_token']
        if not token.get('refresh_token'):raise DomainError('Spotify authorization expired; reconnect',401,'oauth_expired')
        refreshed=await self._token({'grant_type':'refresh_token','refresh_token':token['refresh_token'],'client_id':token['client_id']})
        self._save(name,token['client_id'],refreshed,token['refresh_token'])
        return refreshed['access_token']
