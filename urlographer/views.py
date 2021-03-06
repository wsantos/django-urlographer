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
from django.contrib.sitemaps.views import sitemap as contrib_sitemap
from django.contrib.sitemaps import GenericSitemap


from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.urlresolvers import resolve
from django.http import (
    Http404, HttpResponse, HttpResponseNotFound, HttpResponsePermanentRedirect,
    HttpResponseRedirect)

try:
    # Django => 1.9
    from django.contrib.sites.shortcuts import get_current_site
except ImportError:
    from django.contrib.sites.models import get_current_site

try:
    import newrelic
    import newrelic.agent
except:
    newrelic = False

from .models import URLMap
from .utils import (
    canonicalize_path,
    force_cache_invalidation,
    get_redirect_url_with_query_string,
    get_view,
    should_append_slash
)

settings.URLOGRAPHER_HANDLERS = getattr(settings, 'URLOGRAPHER_HANDLERS', {})


def route(request):
    """
    This view is intended to be mapped to '.*' in your root urlconf.
    It does the following:

    #. Redirect to URL ending in / when appropriate
    #. Redirect to canonical path based on return value of
       :func:`urlographer.utils.canonicalize_path`
    #. Use :meth:`~urlographer.models.URLMapManager.cached_get` to retrieve
       a :class:`~urlographer.models.URLMap` that exactly matches the site and
       path, if it exists
    #. If there is a matching :class:`~urlographer.models.URLMap` with a
       *status_code* of 200, create the response using the *view* and *options*
       specified in its :class:`~urlographer.models.ContentMap`
    #. Use django.http.HttpResponseRedirect for temporary redirects
    #. Use django.http.HttpResponsePermanentRedirect for permanent redirects
    #. Use HttpResponseNotFound for 404s
    #. Otherwise, construct the django.http.HttpResponse with a status matching
       the :class:`~urlographer.models.URLMap`'s *status_code*
    #. Finally, process the response through any handlers matching the
       response.status configured in
       :attr:`~urlographer.views.settings.URLOGRAPHER_HANDLERS`.
    """
    if settings.APPEND_SLASH and not request.path_info.endswith('/'):
        # the code below only works if route is mapped to .*
        with_slash = request.path_info + '/'
        if resolve(with_slash)[0] != route:
            return HttpResponsePermanentRedirect(with_slash)
    canonicalized = canonicalize_path(request.path)
    site = get_current_site(request)
    try:
        url = URLMap.objects.cached_get(
            site, canonicalized,
            force_cache_invalidation=force_cache_invalidation(request))
    except URLMap.DoesNotExist:
        url = URLMap(site=site, path=canonicalized, status_code=404,
                     force_secure=False)

    request.urlmap = url

    if url.force_secure and not request.is_secure():
        url_to = get_redirect_url_with_query_string(request, unicode(url))
        response = HttpResponsePermanentRedirect(url_to)
    elif url.status_code == 200:
        if request.path != canonicalized:
            response = HttpResponsePermanentRedirect(unicode(url))
        else:
            view = get_view(url.content_map.view)
            options = url.content_map.options

            if newrelic:
                view_name = "{}:{}.{}".format(view.__module__,
                                              view.__name__,
                                              request.method.lower())
                newrelic.agent.set_transaction_name(
                    view_name, "Python/urlographer")

            if hasattr(view, 'as_view'):
                initkwargs = options.pop('initkwargs', {})
                response = view.as_view(**initkwargs)(request, **options)
            else:
                response = view(request, **options)

    elif url.status_code == 301:
        response = HttpResponsePermanentRedirect(unicode(url.redirect))
    elif url.status_code == 302:
        response = HttpResponseRedirect(unicode(url.redirect))
    elif url.status_code == 404:
        if should_append_slash(request):
            response = HttpResponsePermanentRedirect(request.path_info + '/')
        else:
            response = HttpResponseNotFound()
    else:
        response = HttpResponse(status=url.status_code)

    handler = settings.URLOGRAPHER_HANDLERS.get(response.status_code, None)
    if handler:
        if callable(handler) or hasattr(handler, 'as_view'):
            view = handler
        elif isinstance(handler, basestring):
            view = get_view(handler)
        else:
            raise ImproperlyConfigured(
                'URLOGRAPHER_HANDLERS values must be views or import strings')

        if newrelic:
            view_name = "{}:{}.{}".format(view.__module__,
                                          view.__name__,
                                          request.method.lower())
            newrelic.agent.set_transaction_name(
                view_name, "Python/urlographer")

        if hasattr(view, 'as_view'):
            response = view.as_view()(request, response)
        else:
            response = view(request, response)

    elif response.status_code == 404:
        raise Http404

    return response


class CustomSitemap(GenericSitemap):

    def get_urls(self, *args, **kwargs):
        urls = super(CustomSitemap, self).get_urls(*args, **kwargs)
        for url in urls:
            url['location'] = unicode(url['item'])
        return urls


def sitemap(request, invalidate_cache=False):
    """
    Constructs a `GenericSitemap <https://docs.djangoproject.com/en/dev/ref/\
    contrib/sitemaps/#django.contrib.sitemaps.GenericSitemap>`_ containing all
    :class:`~urlographer.models.URLMap`\ s with a *status_code* of 200 for the
    current site.

    Caches based on the site and the
    :attr:`~urlographer.models.settings.URLOGRAPHER_CACHE_PREFIX` with a
    timeout based on
    :attr:`~urlographer.models.settings.URLOGRAPHER_CACHE_TIMEOUT`.
    Cache invalidation can be triggered by the invalidate_cache keyword arg or
    the Cache-Control: no-cache header.
    """

    site = get_current_site(request)
    cache_key = '%s%s_sitemap' % (settings.URLOGRAPHER_CACHE_PREFIX, site)
    if not invalidate_cache and not force_cache_invalidation(request):
        cached = cache.get(cache_key)
        if cached:
            return HttpResponse(content=cached)
    response = contrib_sitemap(
        request,
        {'urlmap': CustomSitemap(
            {'queryset': URLMap.objects.filter(
                site=site, status_code=200,
                on_sitemap=True).select_related('site')})})
    response.render()
    cache.set(cache_key, response.content, settings.URLOGRAPHER_CACHE_TIMEOUT)
    return response
