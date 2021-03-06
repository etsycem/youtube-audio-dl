import datetime
import json
import os
import urllib
from urlparse import urlparse, parse_qs, urlsplit, urlunsplit

from django.core.urlresolvers import reverse
from django.core.exceptions import ObjectDoesNotExist
from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseRedirect
from django.utils.encoding import smart_str

from celery.result import AsyncResult

from core.forms import DownloadForm

from api import tasks, utils
from api.models import ActivityLog, YouTube
from api.utils import get_client_ip


def extract_audio(request):
    if request.method == 'POST':
        form = DownloadForm(request.POST)

        if form.is_valid():
            client_ip = utils.get_client_ip(request)

            url = form.cleaned_data['url']

            # Remove the list parameter from the URL as we currently don't
            # support conversion of an entire playlist.
            # TODO: Refactor this entire thing later.
            if url:
                qs = parse_qs(urlparse(url).query)

                if qs.get('list', None):
                    del(qs['list'])
                    parts = urlsplit(url)
                    url = urlunsplit([parts.scheme,
                                      parts.netloc,
                                      parts.path,
                                      urllib.urlencode(qs, True),
                                      parts.fragment])

            task = tasks.extract_audio.delay(url, client_ip)
            result = AsyncResult(task.id)
            result.wait()

            if result.successful():
                data = {'success': True, 'id': task.id}

                if result.result:
                    video_id = result.result['video_id']
                    filename = result.result['filename']
                    download_link = reverse(
                        'download_file',
                        kwargs={'video_id': video_id, 'filename': filename})

                    data['video_id'] = video_id
                    data['filename'] = filename
                    data['download_link'] = download_link

                return HttpResponse(json.dumps(data))
            else:
                return HttpResponse(json.dumps({'success': False}))
        else:
            message = 'Please enter a URL.'
            return HttpResponse(json.dumps({'form_valid': False,
                                            'detail': message}))

    return HttpResponseForbidden


def download_file(request, video_id, filename):
    filepath = '%s%s' % (settings.MEDIA_ROOT, filename)
    file_exists = os.path.exists(filepath)

    youtube = None
    try:
        youtube = YouTube.objects.get(video_id=video_id, audio_filename=filename)
    except ObjectDoesNotExist:
        pass

    if youtube and file_exists:
        ActivityLog.objects.create(
            action='DOWNLOAD',
            client_ip=get_client_ip(request),
            video_id=video_id
        )

        youtube.download_count += 1
        youtube.last_download_date = datetime.datetime.now()
        youtube.save()

        if settings.DEBUG:
            with open(filepath, 'rb') as file_data:
                response = HttpResponse(file_data.read(),
                                        content_type='audio/mpeg')

            response['Content-Disposition'] = 'attachment; filename=%s' % \
                smart_str(filename)
            response['Content-Length'] = os.path.getsize(filepath)

            return response
        else:
            # Have Nginx serve the file in production.
            response = HttpResponse(mimetype='application/force-download')
            response['Content-Length'] = os.path.getsize(filepath)
            response['X-Accel-Redirect'] = '%s%s' % (settings.MEDIA_URL,
                                                     smart_str(filename))

            return response

    return HttpResponseRedirect(reverse('home'))