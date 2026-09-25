"""Fixed native SDK reader; never creates, writes, resizes or closes a pane."""
import contextlib
import json
import sys


def text(value,limit):
    return ''.join(c for c in str(value)[:limit] if c=='\t' or ord(c)>=32 and ord(c)!=127)


async def observe(connection,identity):
    import iterm2
    app=await iterm2.async_get_app(connection)
    panes=[]
    visited=0
    for window in app.windows:
        for tab in window.tabs:
            for session in tab.sessions:
                visited+=1
                if visited>100:
                    if identity:raise ValueError('Native inventory bound exceeded')
                    return {'panes':panes,'truncated':True}
                metadata={'id':session.session_id,'window_id':str(window.window_id),'tab_id':str(tab.tab_id),'title':text(session.name,256),'columns':session.grid_size.width,'rows':session.grid_size.height}
                if identity is None:
                    panes.append(metadata)
                elif session.session_id==identity:
                    contents=await session.async_get_screen_contents()
                    count=min(contents.number_of_lines,500)
                    lines=[text(contents.line(i).string,500) for i in range(count)]
                    return {**metadata,'lines':lines,'cursor':{'x':contents.cursor_coord.x,'y':contents.cursor_coord.y},'truncated':contents.number_of_lines>500 or session.grid_size.width>500}
    return {'missing':True} if identity else {'panes':panes,'truncated':False}


def main():
    if sys.platform!='darwin':return 2
    import iterm2
    result=None
    async def read(connection):
        nonlocal result
        result=await observe(connection,sys.argv[1] or None)
    with contextlib.redirect_stdout(sys.stderr):
        iterm2.run_until_complete(read,retry=False)
    if result is None:return 3
    sys.stdout.write(json.dumps(result,ensure_ascii=True))
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except Exception:sys.exit(1)
