"""Existing HTML entry points."""
from .common import render_template
from .context import ApplicationContext


def install(ctx: ApplicationContext):
    @ctx.app.get('/workspace-preview')
    def workspace_preview():
        return render_template('studio.html', preview=True)
    ctx.workspace_preview = workspace_preview

    @ctx.app.get('/')
    def index():
        return render_template('studio.html'), 200, {'Cache-Control': 'no-cache'}
    ctx.index = index

