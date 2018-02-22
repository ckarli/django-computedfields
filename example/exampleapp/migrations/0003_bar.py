# -*- coding: utf-8 -*-
# Generated by Django 1.11.10 on 2018-02-22 14:26
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('exampleapp', '0002_auto_20180221_2351'),
    ]

    operations = [
        migrations.CreateModel(
            name='Bar',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=32)),
                ('klaus', models.CharField(editable=False, max_length=32)),
                ('foo', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='exampleapp.Foo')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
