# Copyright 2013 Consumers Unified LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from django.conf import settings
from django.core.urlresolvers import get_mod_func

try:
    # Django versions >= 1.9
    from django.utils.lru_cache import lru_cache

    def memoize(function, *args):
        return lru_cache()(function)

except ImportError:
    # Django versions < 1.9
    from django.utils.functional import memoize

try:
    # Django versions >= 1.9
    from django.utils.module_loading import import_module
except ImportError:
    # Django versions < 1.9
    from django.utils.importlib import import_module

_view_cache = {}


def force_ascii(s):
    """
    Eliminate all non-ASCII characters, ignoring errors
    """
    if isinstance(s, unicode):
        return s.encode('ascii', 'ignore')
    else:
        return unicode(s, 'ascii', errors='ignore')


def canonicalize_path(path):
    """
    #. Eliminate extra slashes
    #. Eliminate ./
    #. Make ../ behave as expected by eliminating parent dirs from path
       (but without unintentionally exposing files, of course)
    #. Eliminate all unicode chars using :func:`force_ascii`
    """
    while '//' in path:
        path = path.replace('//', '/')
    if path.startswith('./'):
        path = path[1:]
    elif path.startswith('../'):
        path = path[2:]
    while '/./' in path:
        path = path.replace('/./', '/')
    while '/../' in path:
        pre, post = path.split('/../', 1)
        if pre.startswith('/') and '/' in pre[1:]:
            pre = '/'.join(pre.split('/')[:-1])
            path = '/'.join([pre, post])
        else:
            path = '/' + post
    return force_ascii(path.lower())


def get_view(lookup_view):
    """
    Uses similar logic to django.urlresolvers.get_callable, but always raises
    on failures and supports class based views.
    """
    lookup_view = lookup_view.encode('ascii')
    mod_name, func_or_class_name = get_mod_func(lookup_view)
    assert func_or_class_name != ''
    view = getattr(import_module(mod_name), func_or_class_name)
    assert callable(view) or hasattr(view, 'as_view')
    return view
get_view = memoize(get_view, _view_cache, 1)


def get_redirect_url_with_query_string(request, url):
    query_string = request.META.get('QUERY_STRING', '')
    if query_string:
        return '{}?{}'.format(url, query_string)
    return url


def force_cache_invalidation(request):
    '''
    Returns true if a request from contains the Cache-Control: no-cache header
    '''
    return 'no-cache' in request.META.get('HTTP_CACHE_CONTROL', '')


def should_append_slash(request):
    """
    :return: boolean determining whether the a trailing slash `/`
             should be appended to the request path
    """
    no_redirect = ('/', '.htm', '.html')
    return settings.APPEND_SLASH and not (
        request.path_info.endswith(no_redirect))
