"""Convert Adler's Notes from Nature expedition CSV format."""

import re
import json
from dateutil.parser import parse
import pandas as pd
import lib.util as util

SUBJECT_PREFIX = 'subject_'
STARTED_AT = 'classification_started_at'
USER_NAME = 'user_name'
KEEP_COUNT = 3


def read(args):
    """Read and convert the input CSV data."""
    df = pd.read_csv(args.input_file, dtype=str)

    # Workflows must be processed individually
    workflow_id = get_workflow_id(df, args)

    df = remove_rows_not_in_workflow(df, str(workflow_id))

    get_nfn_only_defaults(df, args, workflow_id)

    # Extract the various json blobs
    column_types = {}
    df = (extract_annotations(df, column_types)
            .pipe(extract_subject_data, column_types)
            .pipe(extract_metadata))

    # Get the subject_id from the subject_ids list, use the first one
    df[args.group_by] = df.subject_ids.map(
        lambda x: int(str(x).split(';')[0]))

    # Remove unwanted columns
    unwanted_columns = [c for c in df.columns
                        if c.lower() in [
                            'user_id',
                            'user_ip',
                            'subject_ids',
                            'subject_data',
                            (SUBJECT_PREFIX + 'retired').lower()]]
    df = df.drop(unwanted_columns, axis=1)
    column_types = {k: v for k, v in column_types.items()
                    if k not in unwanted_columns}

    columns = util.sort_columns(args, df.columns, column_types)
    df = (df.reindex_axis(columns, axis=1)
            .fillna('')
            .sort_values([args.group_by, STARTED_AT])
            .drop_duplicates([args.group_by, USER_NAME], keep='first')
            .groupby(args.group_by).head(KEEP_COUNT))

    return df, column_types


def remove_rows_not_in_workflow(df, workflow_id):
    """Remove all rows not in the dataframe."""
    return df.loc[df.workflow_id == workflow_id, :]


def get_nfn_only_defaults(df, args, workflow_id):
    """Set nfn-only argument defaults."""
    if args.summary:
        workflow_name = get_workflow_name(df)

    if not args.title and args.summary:
        args.title = 'Summary of "{}" ({})'.format(workflow_name, workflow_id)

    if not args.user_column:
        args.user_column = USER_NAME


def get_workflow_id(df, args):
    """Pull the workflow ID from the data-frame if it was not given."""
    if args.workflow_id:
        return args.workflow_id

    workflow_ids = df.workflow_id.unique()

    if len(workflow_ids) > 1:
        util.error_exit('There are multiple workflows in this file. '
                        'You must provide a workflow ID as an argument.')

    return workflow_ids[0]


def get_workflow_name(df):
    """Extract and format the workflow name from the data frame."""
    try:
        workflow_name = df.workflow_name.iloc[0]
        workflow_name = re.sub(r'^[^_]*_', '', workflow_name)
    except KeyError:
        util.error_exit('Workflow name not found in classifications file.')
    return workflow_name


def extract_metadata(df):
    """Extract a few fields from the metadata JSON object."""
    def _extract_date(value):
        return parse(value).strftime('%d-%b-%Y %H:%M:%S')

    data = df.metadata.map(json.loads).tolist()
    data = pd.DataFrame(data, index=df.index)

    df[STARTED_AT] = data.started_at.map(_extract_date)

    name = 'classification_finished_at'
    df[name] = data.finished_at.map(_extract_date)

    return df.drop(['metadata'], axis=1)


def extract_subject_data(df, column_types):
    """
    Extract subject data from the json object in the subject_data column.

    We prefix the new column names with "subject_" to keep them separate from
    the other df columns. The subject data json looks like:
        {<subject_id>: {"key_1": "value_1", "key_2": "value_2", ...}}
    """
    data = (df.subject_data.map(json.loads)
              .apply(lambda x: list(x.values())[0])
              .tolist())
    data = pd.DataFrame(data, index=df.index)
    df = df.drop(['subject_data'], axis=1)

    if 'retired' in data.columns:
        data = data.drop(['retired'], axis=1)

    if 'id' in data.columns:
        data = data.rename(columns={'id': 'external_id'})

    columns = [re.sub(r'\W+', '_', c) for c in data.columns]
    columns = [re.sub(r'^_+|_$', '', c) for c in columns]
    columns = [SUBJECT_PREFIX + c for c in columns]

    columns = {old: new for old, new in zip(data.columns, columns)}
    data = data.rename(columns=columns)

    df = pd.concat([df, data], axis=1)

    # Put the subject columns into the column_types: They're all 'same'
    last = util.last_column_type(column_types)
    for name in data.columns:
        last += 1
        column_types[name] = {'type': 'same', 'order': last, 'name': name}

    return df


def extract_annotations(df, column_types):
    """
    Extract annotations from the json object in the annotations column.

    Annotations are nested json blobs with a peculiar data format.
    """
    data = df.annotations.map(json.loads)
    data = [flatten_annotations(a, column_types) for a in data]
    data = pd.DataFrame(data, index=df.index)

    df = pd.concat([df, data], axis=1)

    return adjust_column_names(df, column_types).drop(['annotations'], axis=1)


def flatten_annotations(annotations, column_types):
    """
    Flatten annotations.

    Annotations are nested json blobs with a peculiar data format. So we
    flatten it to make it easier to work with.

    We also need to consider that some tasks have the same label. In that case
    we add a tie breaker, which is handled in the _key() function.
    """
    def _key(label):
        label = re.sub(r'^\s+|\s+$', '', label)
        i = 1
        base = label
        while label in tasks:
            i += 1
            label = '{} #{}'.format(base, i)
        return label

    def _append_column_type(key, type):
        if key not in column_types:
            last = util.last_column_type(column_types)
            column_types[key] = {'type': type, 'order': last + 1, 'name': key}

    def _flatten(task):
        if isinstance(task.get('value'), list):
            for subtask in task['value']:
                _flatten(subtask)
        elif 'select_label' in task:
            key = _key(task['select_label'])
            option = task.get('option')
            value = task.get('label', '') if option else task.get('value', '')
            tasks[key] = value
            _append_column_type(key, 'select')
        elif 'task_label' in task:
            key = _key(task['task_label'])
            tasks[key] = task.get('value', '')
            _append_column_type(key, 'text')
        else:
            raise ValueError()

    tasks = {}

    for annotation in annotations:
        _flatten(annotation)

    return tasks


def adjust_column_names(df, column_types):
    """Rename columns to add a "#1" suffix if there exists a "#2" suffix."""
    rename = {}
    for name in column_types.keys():
        old_name = name[:-3]
        if name.endswith('#2') and column_types.get(old_name):
            rename[old_name] = old_name + ' #1'

    for old_name, new_name in rename.items():
        new_task = column_types[old_name]
        new_task['name'] = new_name
        column_types[new_name] = new_task
        del column_types[old_name]

    return df.rename(columns=rename)
