import logging
import mimetypes
import os

from wsgiref.util import FileWrapper

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import (
    CreateAPIView,
    ListAPIView,
    RetrieveAPIView,
    RetrieveUpdateAPIView,
    RetrieveUpdateDestroyAPIView,
    get_object_or_404
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings

from django.http import StreamingHttpResponse

import auditor

from api.experiments.serializers import (
    ExperimentCreateSerializer,
    ExperimentDetailSerializer,
    ExperimentJobDetailSerializer,
    ExperimentJobSerializer,
    ExperimentJobStatusSerializer,
    ExperimentMetricSerializer,
    ExperimentSerializer,
    ExperimentStatusSerializer
)
from api.filters import OrderingFilter, QueryFilter
from api.utils.views import AuditorMixinView, ListCreateAPIView
from db.models.experiment_groups import ExperimentGroup
from db.models.experiment_jobs import ExperimentJob, ExperimentJobStatus
from db.models.experiments import Experiment, ExperimentMetric, ExperimentStatus
from event_manager.events.experiment import (
    EXPERIMENT_COPIED_TRIGGERED,
    EXPERIMENT_CREATED,
    EXPERIMENT_DELETED_TRIGGERED,
    EXPERIMENT_JOBS_VIEWED,
    EXPERIMENT_LOGS_VIEWED,
    EXPERIMENT_RESTARTED_TRIGGERED,
    EXPERIMENT_RESUMED_TRIGGERED,
    EXPERIMENT_STATUSES_VIEWED,
    EXPERIMENT_STOPPED_TRIGGERED,
    EXPERIMENT_UPDATED,
    EXPERIMENT_VIEWED
)
from event_manager.events.experiment_group import EXPERIMENT_GROUP_EXPERIMENTS_VIEWED
from event_manager.events.experiment_job import (
    EXPERIMENT_JOB_STATUSES_VIEWED,
    EXPERIMENT_JOB_VIEWED
)
from event_manager.events.project import PROJECT_EXPERIMENTS_VIEWED
from libs.paths.experiments import get_experiment_logs_path
from libs.permissions.authentication import InternalAuthentication
from libs.permissions.internal import IsAuthenticatedOrInternal
from libs.permissions.projects import get_permissible_project
from libs.spec_validation import validate_experiment_spec_config
from libs.utils import to_bool
from polyaxon.celery_api import app as celery_app
from polyaxon.settings import SchedulerCeleryTasks

_logger = logging.getLogger("polyaxon.views.experiments")


class ExperimentListView(ListAPIView):
    """List all experiments"""
    queryset = Experiment.objects.all()
    serializer_class = ExperimentSerializer
    permission_classes = (IsAuthenticated,)


class ProjectExperimentListView(ListCreateAPIView):
    """List/Create an experiment under a project"""
    queryset = Experiment.objects.all()
    serializer_class = ExperimentSerializer
    create_serializer_class = ExperimentCreateSerializer
    permission_classes = (IsAuthenticated,)
    filter_backends = (QueryFilter, OrderingFilter,)
    query_manager = 'experiment'
    ordering = ('-updated_at',)
    ordering_fields = ('created_at', 'updated_at', 'started_at', 'finished_at')

    def get_group(self, project, group_id):
        group = get_object_or_404(ExperimentGroup, project=project, id=group_id)
        auditor.record(event_type=EXPERIMENT_GROUP_EXPERIMENTS_VIEWED,
                       instance=group,
                       actor_id=self.request.user.id)

        return group

    def filter_queryset(self, queryset):
        independent = self.request.query_params.get('independent', None)
        if independent is not None:
            independent = to_bool(independent)
        else:
            independent = False
        group_id = self.request.query_params.get('group', None)
        if independent and group_id:
            raise ValidationError('You cannot filter for independent experiments and '
                                  'group experiments at the same time.')
        project = get_permissible_project(view=self)
        queryset = queryset.filter(project=project)
        if independent is not None and to_bool(independent):
            queryset = queryset.filter(experiment_group__isnull=True)
        if group_id:
            group = self.get_group(project=project, group_id=group_id)
            queryset = queryset.filter(experiment_group=group)
        auditor.record(event_type=PROJECT_EXPERIMENTS_VIEWED,
                       instance=project,
                       actor_id=self.request.user.id)
        return super().filter_queryset(queryset=queryset)

    def perform_create(self, serializer):
        return serializer.save(user=self.request.user, project=get_permissible_project(view=self))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = self.perform_create(serializer)
        auditor.record(event_type=EXPERIMENT_CREATED, instance=instance)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class ExperimentDetailView(AuditorMixinView, RetrieveUpdateDestroyAPIView):
    queryset = Experiment.objects.all()
    serializer_class = ExperimentDetailSerializer
    permission_classes = (IsAuthenticated,)
    lookup_field = 'id'
    instance = None
    get_event = EXPERIMENT_VIEWED
    update_event = EXPERIMENT_UPDATED
    delete_event = EXPERIMENT_DELETED_TRIGGERED

    def filter_queryset(self, queryset):
        return queryset.filter(project=get_permissible_project(view=self))


class ExperimentCloneView(CreateAPIView):
    queryset = Experiment.objects.all()
    serializer_class = ExperimentSerializer
    permission_classes = (IsAuthenticated,)
    lookup_field = 'id'
    event_type = None

    def filter_queryset(self, queryset):
        return queryset.filter(project=get_permissible_project(view=self))

    def clone(self, obj, config, declarations, update_code_reference, description):
        pass

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        auditor.record(event_type=self.event_type,
                       instance=obj,
                       actor_id=self.request.user.id)

        description = None
        config = None
        declarations = None
        update_code_reference = False
        if 'config' in request.data:
            spec = validate_experiment_spec_config(
                [obj.specification.parsed_data, request.data['config']], raise_for_rest=True)
            config = spec.parsed_data
            declarations = spec.declarations
        if 'update_code' in request.data:
            try:
                update_code_reference = to_bool(request.data['update_code'])
            except TypeError:
                raise ValidationError('update_code should be a boolean')
        if 'description' in request.data:
            description = request.data['description']
        new_obj = self.clone(obj=obj,
                             config=config,
                             declarations=declarations,
                             update_code_reference=update_code_reference,
                             description=description)
        serializer = self.get_serializer(new_obj)
        return Response(status=status.HTTP_201_CREATED, data=serializer.data)


class ExperimentRestartView(ExperimentCloneView):
    queryset = Experiment.objects.all()
    serializer_class = ExperimentSerializer
    permission_classes = (IsAuthenticated,)
    lookup_field = 'id'
    event_type = EXPERIMENT_RESTARTED_TRIGGERED

    def clone(self, obj, config, declarations, update_code_reference, description):
        return obj.restart(user=self.request.user,
                           config=config,
                           declarations=declarations,
                           update_code_reference=update_code_reference,
                           description=description)


class ExperimentResumeView(ExperimentCloneView):
    queryset = Experiment.objects.all()
    serializer_class = ExperimentSerializer
    permission_classes = (IsAuthenticated,)
    lookup_field = 'id'
    event_type = EXPERIMENT_RESUMED_TRIGGERED

    def clone(self, obj, config, declarations, update_code_reference, description):
        return obj.resume(user=self.request.user,
                          config=config,
                          declarations=declarations,
                          update_code_reference=update_code_reference,
                          description=description)


class ExperimentCopyView(ExperimentCloneView):
    queryset = Experiment.objects.all()
    serializer_class = ExperimentSerializer
    permission_classes = (IsAuthenticated,)
    lookup_field = 'id'
    event_type = EXPERIMENT_COPIED_TRIGGERED

    def clone(self, obj, config, declarations, update_code_reference, description):
        return obj.copy(user=self.request.user,
                        config=config,
                        declarations=declarations,
                        update_code_reference=update_code_reference,
                        description=description)


class ExperimentViewMixin(object):
    """A mixin to filter by experiment."""
    project = None
    experiment = None

    def get_experiment(self):
        # Get project and check access
        self.project = get_permissible_project(view=self)
        experiment_id = self.kwargs['experiment_id']
        self.experiment = get_object_or_404(Experiment, project=self.project, id=experiment_id)
        return self.experiment

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        return queryset.filter(experiment=self.get_experiment())


class ExperimentStatusListView(ExperimentViewMixin, ListCreateAPIView):
    queryset = ExperimentStatus.objects.order_by('created_at').all()
    serializer_class = ExperimentStatusSerializer
    permission_classes = (IsAuthenticated,)

    def perform_create(self, serializer):
        serializer.save(experiment=self.get_experiment())

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        auditor.record(event_type=EXPERIMENT_STATUSES_VIEWED,
                       instance=self.experiment,
                       actor_id=request.user.id)
        return response


class ExperimentMetricListView(ExperimentViewMixin, ListCreateAPIView):
    queryset = ExperimentMetric.objects.all()
    serializer_class = ExperimentMetricSerializer
    authentication_classes = api_settings.DEFAULT_AUTHENTICATION_CLASSES + [
        InternalAuthentication,
    ]
    permission_classes = (IsAuthenticatedOrInternal,)

    def perform_create(self, serializer):
        serializer.save(experiment=self.get_experiment())


class ExperimentStatusDetailView(ExperimentViewMixin, RetrieveAPIView):
    queryset = ExperimentStatus.objects.all()
    serializer_class = ExperimentStatusSerializer
    permission_classes = (IsAuthenticated,)
    lookup_field = 'uuid'


class ExperimentJobListView(ExperimentViewMixin, ListCreateAPIView):
    queryset = ExperimentJob.objects.order_by('-updated_at').all()
    serializer_class = ExperimentJobSerializer
    create_serializer_class = ExperimentJobDetailSerializer
    permission_classes = (IsAuthenticated,)

    def perform_create(self, serializer):
        serializer.save(experiment=self.get_experiment())

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        auditor.record(event_type=EXPERIMENT_JOBS_VIEWED,
                       instance=self.experiment,
                       actor_id=request.user.id)
        return response


class ExperimentJobDetailView(AuditorMixinView, ExperimentViewMixin, RetrieveUpdateDestroyAPIView):
    queryset = ExperimentJob.objects.all()
    serializer_class = ExperimentJobDetailSerializer
    permission_classes = (IsAuthenticated,)
    lookup_field = 'id'
    get_event = EXPERIMENT_JOB_VIEWED


class ExperimentLogsView(ExperimentViewMixin, RetrieveAPIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, *args, **kwargs):
        experiment = self.get_experiment()
        auditor.record(event_type=EXPERIMENT_LOGS_VIEWED,
                       instance=self.experiment,
                       actor_id=request.user.id)
        log_path = get_experiment_logs_path(experiment.unique_name)

        filename = os.path.basename(log_path)
        chunk_size = 8192
        try:
            wrapped_file = FileWrapper(open(log_path, 'rb'), chunk_size)
            response = StreamingHttpResponse(wrapped_file,
                                             content_type=mimetypes.guess_type(log_path)[0])
            response['Content-Length'] = os.path.getsize(log_path)
            response['Content-Disposition'] = "attachment; filename={}".format(filename)
            return response
        except FileNotFoundError:
            _logger.warning('Log file not found: log_path=%s', log_path)
            return Response(status=status.HTTP_404_NOT_FOUND,
                            data='Log file not found: log_path={}'.format(log_path))


class ExperimentJobViewMixin(object):
    """A mixin to filter by experiment job."""
    project = None
    experiment = None
    job = None

    def get_experiment(self):
        # Get project and check access
        self.project = get_permissible_project(view=self)
        experiment_id = self.kwargs['experiment_id']
        self.experiment = get_object_or_404(Experiment, project=self.project, id=experiment_id)
        return self.experiment

    def get_job(self):
        job_id = self.kwargs['id']
        self.job = get_object_or_404(ExperimentJob,
                                     id=job_id,
                                     experiment=self.get_experiment())
        return self.job

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        return queryset.filter(job=self.get_job())


class ExperimentJobStatusListView(ExperimentJobViewMixin, ListCreateAPIView):
    queryset = ExperimentJobStatus.objects.order_by('created_at').all()
    serializer_class = ExperimentJobStatusSerializer
    permission_classes = (IsAuthenticated,)

    def perform_create(self, serializer):
        serializer.save(job=self.get_job())

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        auditor.record(event_type=EXPERIMENT_JOB_STATUSES_VIEWED,
                       instance=self.job,
                       actor_id=request.user.id)
        return response


class ExperimentJobStatusDetailView(ExperimentJobViewMixin, RetrieveUpdateAPIView):
    queryset = ExperimentJobStatus.objects.all()
    serializer_class = ExperimentJobStatusSerializer
    permission_classes = (IsAuthenticated,)
    lookup_field = 'uuid'


class ExperimentStopView(CreateAPIView):
    queryset = Experiment.objects.all()
    serializer_class = ExperimentSerializer
    permission_classes = (IsAuthenticated,)
    lookup_field = 'id'

    def filter_queryset(self, queryset):
        return queryset.filter(project=get_permissible_project(view=self))

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        auditor.record(event_type=EXPERIMENT_STOPPED_TRIGGERED,
                       instance=obj,
                       actor_id=request.user.id)
        group = obj.experiment_group
        celery_app.send_task(
            SchedulerCeleryTasks.EXPERIMENTS_STOP,
            kwargs={
                'project_name': obj.project.unique_name,
                'project_uuid': obj.project.uuid.hex,
                'experiment_name': obj.unique_name,
                'experiment_uuid': obj.uuid.hex,
                'experiment_group_name': group.unique_name if group else None,
                'experiment_group_uuid': group.uuid.hex if group else None,
                'specification': obj.config,
                'update_status': True
            })
        return Response(status=status.HTTP_200_OK)
