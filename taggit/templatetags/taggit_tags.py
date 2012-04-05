from django import template
from django.db import models
from django.db.models import Count
from django.db.models.loading import get_model
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.conf import settings

from django.contrib.contenttypes import generic

from templatetag_sugar.register import tag
from templatetag_sugar.parser import Name, Variable, Constant, Optional, Model

## retrieve configuration options
## if a config option is undefined, set a sane default value
# parameters describing the tagging model in use
TAG_MODEL = getattr(settings, 'TAGGIT_TAG_MODEL' 'taggit.Tag')
TAGGED_ITEM_MODEL = getattr(settings, 'TAGGIT_TAGGED_ITEM_MODEL' 'taggit.TaggedItem')
TAG_FIELD_RELATED_NAME = getattr(settings, 'TAGGIT_TAG_FIELD_RELATED_NAME' 'taggit_taggeditem_items')
# parameters related to generation of tag-clouds  
MAX_WEIGHT = getattr(settings, 'TAGGIT_TAGCLOUD_MAX_WEIGHT', 6.0)
MIN_WEIGHT = getattr(settings, 'TAGGIT_TAGCLOUD_MIN_WEIGHT', 1.0)

register = template.Library()


def is_generic_tagging(through_model):
    """
    This function takes the "through model"[*] of a given tagging model;
    returns ``True`` if that tagging model is a generic one, ``False`` otherwise.
    
    If the passed argument is not a well-formed through model -- i.e. if it doesn't contain
    a ``content_object`` attribute which is either a ``ForeignKey`` or a ``GenericForeignKey`` --
    raises an ``ImproperlyConfigured`` exception.
    
    
    .. [*] i.e. the Django model which represents a tagged item w.r.t. a given tagging model
    """
    opts = through_model._meta
    err_msg = "The object %s doesn't seem to be a valid through model for tagging" % through_model
    
    if 'content_object' in opts.get_all_field_names():
        field = opts.get_field_by_name('content_object')[0]
        if isinstance(field, models.ForeignKey):
            # the ``content_object`` attribute of the through model is a FK.
            # so we have a model-specific tagging model 
            return False
        else:
            raise ImproperlyConfigured(err_msg)
    else:
        # search for GFKs 
        # since a GFK is a dummy field  -- it doesn't map to an actual DB column --
        # it's not listed among "genuine" model fields, but among so called "virtual fields"
        for field in opts.virtual_fields:       
            if isinstance(field, generic.GenericForeignKey) and field.name == 'content_object': 
                return True
        raise ImproperlyConfigured(err_msg)
    

def get_tag_queryset(forvar=None):
    """
    WRITEME
    """
    # Django model representing tags
    tag_model = get_model(*TAG_MODEL.split('.'))
    # Django model representing tagged items (w.r.t. the tagging model in use)
    through_model = get_model(*TAGGED_ITEM_MODEL.split('.'))
    
    count_field = None

    if forvar is None:
        # get all tags
        qs = TAG_MODEL._default_manager.all()
    else:
        # extract app label and model name
        appl_abel, model = None, None, None
        try:
            beginning, applabel, model = forvar.rsplit('.', 2)
        except ValueError:
            try:
                applabel, model = forvar.rsplit('.', 1)
            except ValueError:
                applabel = forvar
        applabel = applabel.lower()
        
        # filter tagged items        
        if model is None:
            # Get tags for a whole app
            qs = TAGGED_ITEM_MODEL._default_manager.filter(content_type__app_label=app_label)
            tag_ids = qs.values_list('tag_id', flat=True)
            qs = TAG_MODEL._default_manager.filter(id__in=tag_ids)
        else:
            # Get tags for a model
            model = model.lower()
            if ":" in model:
                model, manager_attr = model.split(":", 1)
            else:
                manager_attr = "tags"
            model_class = get_model(applabel, model)
            manager = getattr(model_class, manager_attr)
            queryset = manager.all()
            through_opts = manager.through._meta
            count_field = ("%s_%s_items" % (through_opts.app_label,
                    through_opts.object_name)).lower()

    if count_field is None:
        # Retain compatibility with older versions of Django taggit
        # a version check (for example taggit.VERSION <= (0,8,0)) does NOT
        # work because of the version (0,8,0) of the current dev version of django-taggit
        try:
            return queryset.annotate(num_times=Count(settings.TAG_FIELD_RELATED_NAME))
        except FieldError:
            return queryset.annotate(num_times=Count('taggit_taggeditem_items'))
    else:
        return queryset.annotate(num_times=Count(count_field))


def get_weight_fun(t_min, t_max, f_min, f_max):
    def weight_fun(f_i, t_min=t_min, t_max=t_max, f_min=f_min, f_max=f_max):
        # Prevent a division by zero here, found to occur under some
        # pathological but nevertheless actually occurring circumstances.
        if f_max == f_min:
            mult_fac = 1.0
        else:
            mult_fac = float(t_max-t_min)/float(f_max-f_min)
        return t_max - (f_max-f_i)*mult_fac
    return weight_fun

@tag(register,[Constant('as'), Name(), 
               Optional([Constant('for'), Variable()]), 
               Optional([Constant('limit'), Variable()])
               ])

def get_taglist(context, asvar, forvar=None, limit=10):
    queryset = get_queryset(forvar)         
    queryset = queryset.order_by('-num_times')        
    context[asvar] = queryset
    if limit:
        queryset = queryset[:limit]
    return ''

@tag(register, [Constant('as'), Name(), Optional([Constant('for'), Variable()]), Optional([Constant('limit'), Variable()]),])
def get_tagcloud(context, asvar, forvar=None, limit=None):
    queryset = get_queryset(forvar)
    num_times = queryset.values_list('num_times', flat=True)
    if(len(num_times) == 0):
        context[asvar] = queryset
        return ''
    weight_fun = get_weight_fun(T_MIN, T_MAX, min(num_times), max(num_times))
    queryset = queryset.order_by('name')
    if limit:
        queryset = queryset[:limit]
    for tag in queryset:
        tag.weight = weight_fun(tag.num_times)
    context[asvar] = queryset
    return ''
 
# method from
# https://github.com/dokterbob/django-taggit-templatetags/commit/fe893ac1c93d58cd122c621804f311430c93dc12  
# {% get_similar_obects to product as similar_videos for metaphore.embeddedvideo %}
@tag(register, [Constant('to'), Variable(), Constant('as'), Name(), Optional([Constant('for'), Model()])])
def get_similar_objects(context, tovar, asvar, forvar=None):
    if forvar:
        assert hasattr(tovar, 'tags')
        tags = tovar.tags.all()
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(forvar)
        items = TaggedItem.objects.filter(content_type=ct, tag__in=tags)
        from django.db.models import Count
        ordered = items.values('object_id').annotate(Count('object_id')).order_by()
        ordered_ids = map(lambda x: x['object_id'], ordered)
        objects = ct.model_class().objects.filter(pk__in=ordered_ids)
    else:
        objects = tovar.tags.similar_objects()
    context[asvar] = objects    
    return ''    

    
def include_tagcloud(forvar=None):
    return {'forvar': forvar}

def include_taglist(forvar=None):
    return {'forvar': forvar}
  
register.inclusion_tag('taggit_templatetags/taglist_include.html')(include_taglist)
register.inclusion_tag('taggit_templatetags/tagcloud_include.html')(include_tagcloud)