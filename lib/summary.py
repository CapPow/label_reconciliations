"""Render a summary of the reconciliation process."""

import re
from datetime import datetime
from urllib.parse import urlparse
import pandas as pd
from jinja2 import Environment, PackageLoader
import lib.util as util

# pylint: disable=invalid-name

ROW_TYPES = {  # Row types and their sort order
    'explanations': 'A',
    'reconciled': 'B',
    'unreconciled': 'C'}

# These depend on the patterns put into explanations
NO_MATCH_PATTERN = (r'^(?:No (?:select|text) match on'
                    r'|There (?:was|were) no numbers? in)')
EXACT_MATCH_PATTERN = r'^(?:Exact|Normalized exact) match'
FUZZ_MATCH_PATTERN = r'^(?:Partial|Token set) ratio match'
ALL_BLANK_PATTERN = r'^(?:All|The) \d+ record'
ONESIES_PATTERN = r'^Only 1 transcript in'
MMM_PATTERN = r'^There (?:was|were) (?:\d+) numbers? in'


def report(args, unreconciled, reconciled, explanations, column_types):
    """The main function."""

    # Convert links into anchor elements
    unreconciled = unreconciled.applymap(create_link)
    reconciled = reconciled.applymap(create_link)

    # Get the report template
    env = Environment(loader=PackageLoader('reconcile', '.'))
    template = env.get_template('lib/summary/template.html')

    # Merge the data frames into one data frame in an order the report can use
    merged_cols, merged = merge_dataframes(
        args, unreconciled, reconciled, explanations, column_types)

    # Get transcriber summary data
    transcribers, transcriber_count = user_summary(args, unreconciled)

    # Get transcription problems
    row_problems, problem_options = problems(explanations, column_types)

    # Build the summary report
    summary = template.render(
        args=vars(args),
        header=header_data(args, unreconciled, reconciled),
        row_types=ROW_TYPES,
        reconciled=reconciled_summary(explanations, column_types),
        problem_options=problem_options,
        problems=row_problems,
        transcribers=transcribers,
        transcriber_count=transcriber_count,
        merged_cols=merged_cols,
        merged=merged,
        column_types=column_types)

    # Output the report
    with open(args.summary, 'w') as out_file:
        out_file.write(summary)


def create_link(value):
    """Convert a link into an anchor element."""

    try:
        url = urlparse(value)
        if url.scheme and url.netloc and url.path:
            return '<a href="{}" target="_blank">{}</a>'.format(
                value, value)
    except (ValueError, AttributeError):
        pass
    return value


def merge_dataframes(
        args, unreconciled, reconciled, explanations, column_types):
    """Combine the dataframes so that we can print them out in order for
    the detail report.
    """

    # Make the index a column
    rec = reconciled.reset_index()
    exp = explanations.reset_index()
    unr = unreconciled.astype(object).copy()

    # We want the detail rows to come out in this order
    rec['row_type'] = ROW_TYPES['reconciled']
    exp['row_type'] = ROW_TYPES['explanations']
    unr['row_type'] = ROW_TYPES['unreconciled']

    # Merge and format the dataframes
    merged = pd.concat([rec, exp, unr]).fillna('')
    merged = util.sort_columns(args, merged, column_types)
    merged.sort_values(
        [args.group_by, 'row_type', args.key_column], inplace=True)

    return merged.columns, merged.groupby(args.group_by)


def user_summary(args, unreconciled):
    """Get a list of users and how many transcriptions they did."""

    series = unreconciled.groupby(args.user_column)
    series = series[args.user_column].count()
    series.sort_values(ascending=False, inplace=True)
    transcribers = [{'name': name, 'count': count}
                    for name, count in series.iteritems()]
    return transcribers, len(transcribers)


def header_data(args, unreconciled, reconciled):
    """Data that goes into the report header."""

    title = args.title if args.title else args.input_file
    return {
        'date': datetime.strftime(datetime.now(), '%Y-%m-%d %H:%M'),
        'title': title,
        'ratio': unreconciled.shape[0] / reconciled.shape[0],
        'subjects': reconciled.shape[0],
        'transcripts': unreconciled.shape[0]}


def reconciled_summary(explanations, column_types):
    """Build a summary of how each field was reconciled."""

    how_reconciled = []
    for col in order_column_names(explanations, column_types):

        col_type = column_types.get(col, {'type': 'text'})['type']

        num_fuzzy_match = ''
        if col_type == 'text':
            num_fuzzy_match = '{:,}'.format(explanations[
                explanations[col].str.contains(FUZZ_MATCH_PATTERN)].shape[0])

        num_no_match = explanations[
            explanations[col].str.contains(NO_MATCH_PATTERN)].shape[0]

        num_mmm = ''
        if col_type == 'mmm':
            num_mmm = '{:,}'.format(explanations[
                explanations[col].str.contains(MMM_PATTERN)].shape[0])

        how_reconciled.append({
            'name': col,
            'col_type': col_type,
            'num_no_match': num_no_match,
            'num_fuzzy_match': num_fuzzy_match,
            'num_reconciled': explanations.shape[0] - num_no_match,
            'num_exact_match': explanations[
                explanations[col].str.contains(EXACT_MATCH_PATTERN)].shape[0],
            'num_all_blank': explanations[
                explanations[col].str.contains(ALL_BLANK_PATTERN)].shape[0],
            'num_onesies': explanations[
                explanations[col].str.contains(ONESIES_PATTERN)].shape[0],
            'num_mmm': num_mmm})
    return how_reconciled


def order_column_names(df, column_types):
    """Return explanation columns in the same order as merged data frame."""

    columns = [v['name'] for v
               in sorted(column_types.values(), key=lambda x: x['order'])
               if v['name'] in df.columns]
    return columns


def problems(explanations, column_types):
    """Make a list of problems for each subject."""

    probs = {}
    opts = None

    pattern = '|'.join([NO_MATCH_PATTERN, ONESIES_PATTERN])
    for group_by, cols in explanations.iterrows():

        # Get the list of possible problems
        if not opts:
            opts = [('problem-{}'.format(i), k) for i, (k, v)
                    in enumerate(cols.iteritems(), 1)]
            opts = sorted(opts, key=lambda x: column_types[x[1]]['order'])

        # get the row's problems
        probs[group_by] = {}
        for i, (col, value) in enumerate(cols.iteritems(), 1):
            if re.search(pattern, value):
                probs[group_by][col] = 'problem-{}'.format(i)

    return probs, opts