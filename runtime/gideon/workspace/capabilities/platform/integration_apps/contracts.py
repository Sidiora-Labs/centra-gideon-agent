"""Typed requests for Jira Cloud, Datadog logs and GitHub repositories."""
import re
from datetime import datetime
from urllib.parse import quote,urlsplit
from gideon.workspace.capabilities.music.store import DomainError,integer,text

DD_SITES=('api.datadoghq.com','api.datadoghq.eu','api.us3.datadoghq.com','api.us5.datadoghq.com','api.ap1.datadoghq.com','api.ap2.datadoghq.com','api.uk1.datadoghq.com','api.ddog-gov.com','api.us2.ddog-gov.com')
MUTATIONS={'jira_create','jira_update','jira_comment','jira_transition','jira_delete','jira_sprint_assign','github_archive','github_secret_sync'}
OPERATIONS={'jira_auth':'jira','jira_projects':'jira','jira_search':'jira','jira_issue':'jira','jira_boards':'jira','jira_sprints':'jira','jira_epics':'jira','jira_epic_children':'jira','jira_transitions':'jira','jira_create':'jira','jira_update':'jira','jira_comment':'jira','jira_transition':'jira','jira_delete':'jira','jira_sprint_assign':'jira','datadog_auth':'datadog','datadog_errors':'datadog','github_auth':'github','github_repos':'github','github_archive':'github','github_secret_sync':'github','github_secrets':'github'}


def fields(data,required,optional=()):
    if not isinstance(data,dict) or set(data)-set(required)-set(optional) or set(required)-set(data):raise DomainError('Invalid integration fields')


def configuration(data):
    fields(data,('kind','label','endpoint','credential_name','aux_credential_name','username'))
    if data['kind'] not in ('jira','datadog','github'):raise DomainError('Unknown integration kind')
    for key,limit in (('label',120),('credential_name',100),('aux_credential_name',100),('username',200)):text(data[key],key,limit,key in ('label','credential_name'))
    url=urlsplit(data['endpoint'])
    if url.scheme!='https' or url.username or url.password or url.port or url.path not in ('','/') or url.query or url.fragment:raise DomainError('Documented HTTPS API origin required')
    host=url.hostname or ''
    if data['kind']=='jira' and (not re.fullmatch(r'[a-z0-9-]+\.atlassian\.net',host) or not data['username']):raise DomainError('Jira Cloud tenant and email required')
    if data['kind']=='datadog' and (host not in DD_SITES or not data['aux_credential_name']):raise DomainError('Datadog API site and application credential required')
    if data['kind']=='github' and host!='api.github.com':raise DomainError('GitHub API origin required')
    return {**data,'endpoint':'https://'+host}


def ident(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value):raise DomainError('Invalid remote identifier')
    return quote(value,safe='')


def repository(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}',value) or any(part in ('.','..') for part in value.split('/')):raise DomainError('Expected owner/repository')
    return value


def adf(value):return {'type':'doc','version':1,'content':[{'type':'paragraph','content':[{'type':'text','text':text(value,'issue text',20000,True)}]}]}


def plan(kind,operation,data):
    if OPERATIONS.get(operation)!=kind:raise DomainError('Operation does not belong to connection')
    method='GET';body=None;params={};path=''
    if operation in ('jira_auth','datadog_auth','github_auth'):
        fields(data,());path={'jira':'/rest/api/3/myself','datadog':'/api/v1/validate','github':'/user'}[kind]
    elif operation in ('jira_projects','jira_boards','jira_sprints','github_repos','github_secrets'):
        required={'jira_projects':(),'jira_boards':('project',),'jira_sprints':('board_id',),'github_repos':(),'github_secrets':('repository',)}[operation];fields(data,required,('page',))
        page=integer(data.get('page',0),'page',0,10000)
        if operation=='github_repos':path='/user/repos';params={'per_page':100,'page':page+1,'sort':'full_name'}
        elif operation=='github_secrets':path='/repos/'+repository(data['repository'])+'/actions/secrets';params={'per_page':100,'page':page+1}
        else:
            path='/rest/api/3/project/search' if operation=='jira_projects' else '/rest/agile/1.0/board' if operation=='jira_boards' else '/rest/agile/1.0/board/'+ident(str(data['board_id']))+'/sprint'
            params={'startAt':page*50,'maxResults':50}
            if operation=='jira_boards':params['projectKeyOrId']=ident(data['project'])
    elif operation in ('jira_search','jira_epics','jira_epic_children'):
        fields(data,('jql',) if operation=='jira_search' else ('project',) if operation=='jira_epics' else ('epic',),('cursor',))
        jql=text(data['jql'],'JQL',4000,True) if operation=='jira_search' else 'project = "'+ident(data['project'])+'" AND issuetype = Epic' if operation=='jira_epics' else 'parent = "'+ident(data['epic'])+'"'
        path='/rest/api/3/search/jql';params={'jql':jql,'maxResults':100,'fields':'summary,status,assignee,issuetype,project,updated'}
        if data.get('cursor'):params['nextPageToken']=text(data['cursor'],'cursor',2000,True)
    elif operation in ('jira_issue','jira_transitions','jira_delete'):
        fields(data,('issue',));path='/rest/api/3/issue/'+ident(data['issue'])+('/transitions' if operation=='jira_transitions' else '');method='DELETE' if operation=='jira_delete' else 'GET'
    elif operation in ('jira_create','jira_update'):
        fields(data,('project','issue_type','summary','description') if operation=='jira_create' else ('issue','summary','description','labels'))
        method='POST' if operation=='jira_create' else 'PUT';path='/rest/api/3/issue'+('' if operation=='jira_create' else '/'+ident(data['issue']))
        body={'fields':{'summary':text(data['summary'],'summary',255,True),'description':adf(data['description'])}}
        if operation=='jira_create':body['fields'].update(project={'key':ident(data['project'])},issuetype={'id':ident(data['issue_type'])})
        else:
            if not isinstance(data['labels'],list) or len(data['labels'])>50:raise DomainError('At most fifty labels')
            body['fields']['labels']=[ident(label) for label in data['labels']]
    elif operation in ('jira_comment','jira_transition','jira_sprint_assign'):
        fields(data,('issue','comment') if operation=='jira_comment' else ('issue','transition_id') if operation=='jira_transition' else ('sprint_id','issues'))
        method='POST'
        if operation=='jira_comment':path='/rest/api/3/issue/'+ident(data['issue'])+'/comment';body={'body':adf(data['comment'])}
        elif operation=='jira_transition':path='/rest/api/3/issue/'+ident(data['issue'])+'/transitions';body={'transition':{'id':ident(data['transition_id'])}}
        else:
            if not isinstance(data['issues'],list) or not 1<=len(data['issues'])<=50:raise DomainError('One to fifty sprint issues required')
            path='/rest/agile/1.0/sprint/'+ident(str(data['sprint_id']))+'/issue';body={'issues':[ident(issue) for issue in data['issues']]}
    elif operation=='datadog_errors':
        fields(data,('service','environment','from','to'),('cursor',))
        for name in ('service','environment'):
            text(data[name],name,200,True)
            if any(character in data[name] for character in ('"','\\','\n','\r')):raise DomainError('Invalid log filter literal')
        try:
            start,end=(datetime.fromisoformat(data[key].replace('Z','+00:00')) for key in ('from','to'))
            if start.tzinfo is None or end.tzinfo is None or not 0<(end-start).total_seconds()<=31*86400:raise ValueError()
        except (ValueError,TypeError,AttributeError):raise DomainError('Log window requires timezone and at most31 days')
        method='POST';path='/api/v2/logs/events/search';body={'filter':{'query':f'status:error service:"{data["service"]}" env:"{data["environment"]}"','from':data['from'],'to':data['to']},'sort':'timestamp','page':{'limit':100}}
        if data.get('cursor'):body['page']['cursor']=text(data['cursor'],'cursor',2000,True)
    elif operation=='github_archive':
        fields(data,('repository','archived'))
        if type(data['archived']) is not bool:raise DomainError('archived must be boolean')
        method='PATCH';path='/repos/'+repository(data['repository']);body={'archived':data['archived']}
    elif operation=='github_secret_sync':
        fields(data,('repository','name','credential_ref'))
        if not isinstance(data['name'],str) or not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,99}',data['name']) or data['name'].startswith('GITHUB_'):raise DomainError('Invalid Actions secret name')
        text(data['credential_ref'],'credential reference',100,True);method='PUT';path='/repos/'+repository(data['repository'])+'/actions/secrets/'+data['name'];body={'credential_ref':data['credential_ref']}
    return {'operation':operation,'method':method,'path':path,'params':params,'body':body,'mutates':operation in MUTATIONS}
