from django import template
from django.db import models
from django.db.models import Count
from django.db.models.loading import get_model
from django.db.models.query import QuerySet
from django.core.exceptions import ImproperlyConfigured
from django.conf import settings

from django.contrib.contenttypes import generic
from django.contrib.contenttypes.models import ContentType

from generic_aggregation import generic_annotate

import re

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
    
    
    .. [*] i.e. the Django model which represents a tagged item w.r.t. a given tagging model.
    """
    opts = through_model._meta
    err_msg = "The object %s doesn't seem to be a valid through model for tagging" % through_model
    
    if 'content_object' in opts.get_all_field_names():
        field = opts.get_field_by_name('content_object')[0]
        if isinstance(field, models.ForeignKey):
            # the ``content_object`` attribute of the through model is a FK,
            # so we have a model-specific tagging model 
            return False
        else:
            raise ImproperlyConfigured(err_msg)
    else:
        # search for GFKs 
        # since a GFK is a dummy field  -- it doesn't map to an actual DB column --
        # it's not listed together with "genuine" model fields, but among so-called "virtual fields"
        for field in opts.virtual_fields:       
            if isinstance(field, generic.GenericForeignKey) and field.name == 'content_object': 
                return True
        raise ImproperlyConfigured(err_msg)

 
    
@register.tag(name="get_tag_list")
def do_get_tag_list(parser, token):
    try:
        # Splitting by ``None`` == splitting by spaces
        tag_name, args = token.contents.split(None, 1)
    except ValueError:
        raise template.TemplateSyntaxError("'%r' tag requires at least an 'as' clause" % token.contents.split()[0])
    tag_signature = r'''^\s*                                # match the start of the string 
                     (?P<for_clause>for\s+[.'"\w]+)?\s+     # optional 'for' clause
                     (?P<limit_clause>limit\s+\d+)?\s+      # optional 'limit' clause
                     (?P<as_clause>as\s+\w+)\s*             # mandatory 'as' clause
                     $                                      # match the end of the string
                     '''
    tag_signature = re.compile(tag_signature, re.VERBOSE)
    m = re.search(tag_signature, args)
    if not m: # bad syntax for tag arguments 
        raise template.TemplateSyntaxError("%s tag has been called with invalid arguments: %s" % tag_name, args)
    bits = m.groupdict() # dictionary of matched substrings, keyed by group names
    
    forvar = bits['for_clause'].split()[1]        
    limitvar = bits['limit_clause'].split()[1]
    asvar = bits['as_clause'].split()[1]
    
    app_label, model_name, content_qs_var, limit = (None,) * 4 
    
    if forvar:    
        if forvar[0] == forvar[-1] and forvar[0] in ('"', "'"): # quote-enclosed arg
            forvar = forvar[1:-1] # strip quotes from arg
            # since ``for`` clause's argument is quoted, we intepret it 
            # as a string of the form ``app_label`` or ``app_label.model_name``
            if '.' not in forvar:
                app_label = forvar
            else:
                try:
                    app_label, model_name = forvar.split('.')
                except ValueError:
                    msg = "%s isn't a valid value for the 'for' clause of tag %s" % forvar, tag_name
                    raise template.TemplateSyntaxError(msg)
        else: 
            # since ``for`` clause's argument isn't quoted, we intepret it as a context variable 
            # holding a QuerySet
            content_qs_var = forvar         
    
    if limitvar:
        try:
            limit = int(limitvar)
            if limit <= 0: raise ValueError 
        except ValueError:
            msg = "%s isn't a valid value for the 'limit' clause of tag %s" % limitvar, tag_name
            raise template.TemplateSyntaxError(msg)

    return TagListNode(asvar, app_label, model_name, content_qs_var, limit)
             

class TagListNode(template.Node):
    def __init__(self, asvar, app_label, model_name, content_qs_var, limit):
        self.asvar = asvar
        self.app_label = app_label and app_label.lower() or None
        self.model_name = model_name and model_name.lower() or None
        self.content_qs_var = content_qs_var
        self.limit = limit
    
    def render(self, context):
        try:
            # Django model representing tags
            tag_model = get_model(*TAG_MODEL.split('.'))
            # Django model representing tagged items (w.r.t. the tagging model in use)
            through_model = get_model(*TAGGED_ITEM_MODEL.split('.'))
            through_opts = through_model._meta
            
            if self.content_qs_var:
                    self.content_qs = template.Variable(self.content_qs_var).resolve(context)
            
            # determine what kind of tagging model is in use
            if is_generic_tagging(through_model):
                ##-------- generic tagging model --------##
                ## the ``QuerySet`` -- of tags -- to be annotated
                tag_qs = tag_model.objects.all()
                ## the ``QuerySet`` -- of generic models -- the annotation is performed against 
                # start with a QuerySet comprising every tagged item in the DB  
                generic_qs = through_model.objects.all()
                if self.app_label and self.model_name:
                    # filter away tagged items that aren't instances of the given model
                    ct = ContentType.objects.get(app_label=self.app_label, model=self.model_name)
                    generic_qs = generic_qs.filter(content_type=ct)
                elif self.app_label:
                    # filter away tagged items not belonging to the given app
                    generic_qs = generic_qs.filter(content_type__app_label=self.app_label)
                # restrict annotation to a subset of tagged content objects
                if hasattr(self, 'content_qs'):
                    generic_qs = generic_qs.filter(content_object__in=self.content_qs)
                annotated_tag_qs = generic_annotate(tag_qs, through_model.content_object, Count('pk'), generic_qs, desc=True, alias='count')               
            else:       
                ##------- model-specific tagging model ---------##
                content_model = through_opts.get_field_by_name('content_object')[0].rel.to
                if self.app_label:
                    if self.app_label != content_model._meta.app_label.lower():
                        # wrong app label !
                        return ''
                    if self.model_name:
                        if self.model_name != content_model._meta.object_name.lower():
                            # wrong model name !
                            return ''
                # start with a QuerySet comprising every tag in the DB
                qs = tag_model.objects.all()
                if self.content_qs_var:
                    content_qs = template.Variable(self.content_qs_var).resolve(context)
                    if isinstance(content_qs, QuerySet) and (content_qs.model == content_model): # sanity check
                        lookup_field = '%s__content_object__in' % through_opts.object_name.lower()
                        lookup_args = {lookup_field: content_qs}
                        qs = qs.filter(**lookup_args)  
                    else:
                        # invalid QuerySet
                        return ''
                aggregate_field = through_opts.get_field_by_name('tag')[0].rel.relname
                qs = qs.annotate(count=Count(aggregate_field))
                if self.limit:
                    qs=qs[:self.limit]
                annotated_tag_qs = qs.order_by('-count')
            context[self.asvar] = annotated_tag_qs
        except: # fail silently on rendering
            return ''

@register.tag(name="get_tag_cloud")
def do_get_tag_cloud(parser, token):
    pass

class TagCloudNode(template.Node):
    def __init__(self):
        pass
    
    def render(self, context):
        return ''
