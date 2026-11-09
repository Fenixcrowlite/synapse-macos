"""Sessionless MCP Streamable HTTP JSON response transport (2025-06-18)."""
import json
from . import tools
PROTOCOL='2025-06-18'


def rpc(request):
    if not isinstance(request,dict) or request.get('jsonrpc')!='2.0':
        return {'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid Request'}}
    ident=request.get('id')
    method=request.get('method')
    if method=='notifications/initialized': return None
    try:
        if method=='initialize':
            result={'protocolVersion':PROTOCOL,'capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'synapse-macos','version':'1.1.0'}}
        elif method=='ping': result={}
        elif method=='tools/list': result={'tools':tools.describe()}
        elif method=='tools/call':
            params=request.get('params',{})
            if not isinstance(params,dict): raise ValueError('Invalid params')
            try:
                data=tools.execute(params.get('name'),params.get('arguments',{}))
                if isinstance(data,dict) and '__image__' in data:
                    content=[{'type':'image','data':data['__image__'],'mimeType':data['mimeType']}]
                else: content=[{'type':'text','text':json.dumps(data,ensure_ascii=False)}]
                result={'content':content,'isError':False}
            except (ValueError,PermissionError,FileNotFoundError,OSError,RuntimeError) as exc:
                result={'content':[{'type':'text','text':f'{type(exc).__name__}: {exc}'}],'isError':True}
        else: return {'jsonrpc':'2.0','id':ident,'error':{'code':-32601,'message':'Method not found'}}
        if 'id' not in request: return None
        return {'jsonrpc':'2.0','id':ident,'result':result}
    except Exception as exc:
        return {'jsonrpc':'2.0','id':ident,'error':{'code':-32602,'message':str(exc)}}
