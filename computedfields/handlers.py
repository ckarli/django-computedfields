"""
Module containing the database signal handlers.

The handlers are registered during application startup
in ``apps.ready``.

.. NOTE::

    The handlers are not registered in the managment
    commands ``makemigrations``, ``migrate`` and ``help``.
"""
from threading import local
from django.db.models.fields.reverse_related import ManyToManyRel
from .resolver import active_resolver


# thread local storage to hold
# the pk lists for deletes/updates
STORAGE = local()
STORAGE.DELETES = {}
STORAGE.M2M_REMOVE = {}
STORAGE.M2M_CLEAR = {}
STORAGE.UPDATE_OLD = {}

DELETES = STORAGE.DELETES
M2M_REMOVE = STORAGE.M2M_REMOVE
M2M_CLEAR = STORAGE.M2M_CLEAR
UPDATE_OLD = STORAGE.UPDATE_OLD


def get_old_handler(sender, instance, **kwargs):
    """
    ``get_old_handler`` handler.

    ``pre_save`` signal handler to spot incoming fk relation changes.
    This is needed to correctly update old relations after fk changes,
    that would contain dirty computed field values after a save.
    The actual updates on old relations are done during ``post_save``.
    Skipped during fixtures.
    """
    # do not handle fixtures
    if kwargs.get('raw'):
        return
    # exit early if instance is new
    if instance._state.adding:
        return
    contributing_fks = active_resolver._fk_map.get(sender)
    # exit early if model contains no contributing fk fields
    if not contributing_fks:
        return
    candidates = set(contributing_fks)
    if kwargs.get('update_fields'):
        candidates &= kwargs.get('update_fields')
    # exit early if no contributing fk field will be updated
    if not candidates:
        return
    # we got an update instance with possibly dirty fk fields
    # we do simply a full update on all old related fk records for now
    # FIXME: this might turn out as a major update bottleneck, if so
    #        filter by individual field changes instead? (tests are ~10% slower)
    data = active_resolver.preupdate_dependent(instance, sender)
    if data:
        UPDATE_OLD[instance] = data
    return


def postsave_handler(sender, instance, **kwargs):
    """
    ``post_save`` handler.

    Directly updates dependent objects.
    Skipped during fixtures.
    """
    # do not update for fixtures
    if not kwargs.get('raw'):
        active_resolver.update_dependent(
            instance, sender, kwargs.get('update_fields'),
            old=UPDATE_OLD.pop(instance, []), update_local=False
        )


def predelete_handler(sender, instance, **_):
    """
    ``pre_delete`` handler.

    Gets all dependent objects as pk lists and saves
    them in thread local storage.
    """
    # get the querysets as pk lists to hold them in storage
    # we have to get pks here since the queryset will be empty after deletion
    data = active_resolver._querysets_for_update(sender, instance, pk_list=True)
    if data:
        DELETES[instance] = data


def postdelete_handler(sender, instance, **kwargs):
    """
    ``post_delete`` handler.

    Loads the dependent objects from the previously saved pk lists
    and updates them.
    """
    # after deletion we can update the associated computed fields
    updates = DELETES.pop(instance, {})
    for model, data in updates.items():
        pks, fields = data
        qs = model.objects.filter(pk__in=pks)
        active_resolver.bulk_updater(qs, fields)


def merge_pk_maps(mm1, mm2):
    """
    Add mm2 onto mm1.
    """
    for model, data in mm2.items():
        m2_pks, m2_fields = data
        m1_pks, m1_fields = mm1.setdefault(model, [set(), set()])
        m1_pks.update(m2_pks)
        m1_fields.update(m2_fields)
    return mm1


def m2m_handler(sender, instance, **kwargs):
    """
    ``m2m_change`` handler.

    Works like the other handlers but on the corresponding
    m2m actions.

    .. NOTE::
        The handler triggers updates for both ends of the m2m
        relation, which might lead to massive updates and thus
        heavy time consuming database interaction.
    """
    # since the graph does not handle the m2m through model
    # we have to trigger updates for both ends
    action = kwargs.get('action')
    model = kwargs['model']

    if action == 'post_add':
        pks = kwargs['pk_set']
        # old code
        #active_resolver.update_dependent_multi(
        #    [instance, model.objects.filter(pk__in=pks)], update_local=False)

        # temporary fix to derive m2m relational fields
        # FIXME: move field resolution into resolver map
        m2m_fields = [f for f in type(instance)._meta.get_fields() if f.many_to_many]
        for f in m2m_fields:
            through = f.through if hasattr(f, 'through') else f.remote_field.through
            if through == sender:
                active_resolver.update_dependent(instance, update_fields=[f.name], update_local=False)
                break
        m2m_fields2 = [f for f in model._meta.get_fields() if f.many_to_many]
        for f in m2m_fields2:
            through = f.through if hasattr(f, 'through') else f.remote_field.through
            if through == sender:
                active_resolver.update_dependent(model.objects.filter(pk__in=pks), update_fields=[f.name], update_local=False)
                break

    elif action == 'pre_remove':
        # instance updates
        data = active_resolver._querysets_for_update(
            type(instance), instance, pk_list=True)
        # other side updates
        pks = kwargs['pk_set']
        other = active_resolver._querysets_for_update(
            model, model.objects.filter(pk__in=pks), pk_list=True)
        if other:
            merge_pk_maps(data, other)
        # final
        if data:
            M2M_REMOVE[instance] = data

    elif action == 'post_remove':
        updates = M2M_REMOVE.pop(instance, {})
        for model, data in updates.items():
            pks, fields = data
            qs = model.objects.filter(pk__in=pks)
            active_resolver.bulk_updater(qs, fields)

    elif action == 'pre_clear':
        # instance updates
        data = active_resolver._querysets_for_update(type(instance), instance, pk_list=True)

        # other side updates
        # geez - have to get pks of other side ourself
        inst_model = type(instance)
        if kwargs['reverse']:
            rel = list(filter(
                lambda f: isinstance(f, ManyToManyRel) and f.through == sender,
                inst_model._meta.get_fields()
            ))[0]
            other = active_resolver._querysets_for_update(
                model, getattr(instance, rel.name).all(), pk_list=True)
        else:
            field = list(filter(
                lambda f: isinstance(f, ManyToManyRel) and f.through == sender,
                model._meta.get_fields()
            ))[0]
            other = active_resolver._querysets_for_update(
                model, getattr(instance, field.remote_field.name).all(), pk_list=True)
        if other:
            merge_pk_maps(data, other)

        # final
        if data:
            M2M_CLEAR[instance] = data

    elif action == 'post_clear':
        updates = M2M_CLEAR.pop(instance, {})
        for model, data in updates.items():
            pks, fields = data
            qs = model.objects.filter(pk__in=pks)
            active_resolver.bulk_updater(qs, fields)
