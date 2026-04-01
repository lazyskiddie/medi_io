from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="TrainingData",
            fields=[
                ("id",          models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source",      models.CharField(default="admin", max_length=50)),
                ("filename",    models.CharField(blank=True, max_length=200)),
                ("val_count",   models.IntegerField(default=0)),
                ("values_json", models.TextField(default="{}")),
                ("features",    models.TextField(default="[]")),
                ("created_at",  models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "training_data", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="UserUpload",
            fields=[
                ("id",          models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("filename",    models.CharField(blank=True, max_length=200)),
                ("val_count",   models.IntegerField(default=0)),
                ("flagged_cnt", models.IntegerField(default=0)),
                ("ml_score",    models.IntegerField(blank=True, null=True)),
                ("values_json", models.TextField(default="{}")),
                ("created_at",  models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "user_uploads", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ModelWeights",
            fields=[
                ("model_id",      models.CharField(default="current", max_length=20, primary_key=True, serialize=False)),
                ("weights_json",  models.TextField(default="[]")),
                ("stats_json",    models.TextField(default="{}")),
                ("version",       models.IntegerField(default=1)),
                ("training_size", models.IntegerField(default=0)),
                ("trained_at",    models.DateTimeField(blank=True, null=True)),
            ],
            options={"db_table": "model_weights"},
        ),
        migrations.CreateModel(
            name="BatchJob",
            fields=[
                ("id",         models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("total",      models.IntegerField(default=0)),
                ("processed",  models.IntegerField(default=0)),
                ("saved",      models.IntegerField(default=0)),
                ("skipped",    models.IntegerField(default=0)),
                ("failed",     models.IntegerField(default=0)),
                ("status",     models.CharField(choices=[("pending","Pending"),("running","Running"),("done","Done"),("error","Error")], default="pending", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "batch_jobs", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="BatchItem",
            fields=[
                ("id",          models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("job",         models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="core.batchjob")),
                ("filename",    models.CharField(max_length=200)),
                ("status",      models.CharField(choices=[("waiting","Waiting"),("processing","Processing"),("ready","Ready for review"),("saved","Saved"),("skipped","Skipped"),("failed","Failed")], default="waiting", max_length=20)),
                ("val_count",   models.IntegerField(default=0)),
                ("values_json", models.TextField(default="{}")),
                ("error",       models.TextField(blank=True)),
            ],
            options={"db_table": "batch_items"},
        ),
    ]
