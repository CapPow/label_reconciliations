"""Reconcile free text fields using username based weights."""

import re
from collections import namedtuple
from itertools import combinations
from fuzzywuzzy import fuzz
import inflect
import argparse

E = inflect.engine()
E.defnoun('The', 'All')
P = E.plural

FuzzyRatioScore = namedtuple('FuzzyRatioScore', 'score value')
FuzzySetScore = namedtuple('FuzzySetScore', 'score value tokens')
ExactScore = namedtuple('ExactScore', 'value count')

def reconcile(group, args=None):
    """Reconcile the data."""
    if args.user_weights:
        trustedUserWeights = {key: int(value) for key, value in [(i.split(':')) for i in args.user_weights.split(',')]}
    else:
        trustedUserWeights = {}
    values = ['\n'.join([' '.join(ln.split()) for ln in str(g).splitlines()])
              for g in group]
                   
    filled = only_filled_values(values)

    count = len(values)
    blanks = count - sum([f.count for f in filled])

    if not filled:
        reason = '{} {} {} {} blank'.format(
            P('The', count), count, P('record', count), P('is', count))
        return reason, ''

    if filled[0].count > 1 and filled[0].count == count:
        reason = 'Normalized unanimous match, {} of {} {}'.format(
            filled[0].count, count, P('record', count))
        return reason, filled[0].value

    if filled[0].count > 1:
        reason = 'Normalized majority match, {} of {} {} with {} {}'.format(
            filled[0].count, count, P('record', count),
            blanks, P('blank', blanks))
        return reason, filled[0].value

    if len(filled) == 1:
        reason = 'Only 1 transcript in {} {}'.format(count, P('record', count))
        return reason, filled[0].value

    # Check for simple in-place fuzzy matches
    top = top_partial_ratio(group, trustedUserWeights) # passing in group instead of values
    if top.score >= args.fuzzy_ratio_threshold:
        reason = 'Partial ratio match on {} {} with {} {}, score={}'.format(
            count, P('record', count), blanks, P('blank', blanks), top.score)
        return reason, top.value

    # Now look for the best token match
    top = top_token_set_ratio(values)
    if top.score >= args.fuzzy_set_threshold:
        reason = 'Token set ratio match on {} {} with {} {}, score={}'.format(
            count, P('record', count), blanks, P('blank', blanks), top.score)
        return reason, top.value

    reason = 'No text match on {} {} with {} {}'.format(
        count, P('record', count), blanks, P('blank', blanks))
    return reason, ''


def only_filled_values(values):
    """Get the filled items items in the group.

    Then sort them by frequency. Normalize the text for comparison by removing
    spaces and punctuation, and setting all letters to lower case. We return
    the longest the same value ("atestlabel") but we will return the second
    one:
      "A test label"  "a test label."   "A TEST LABEL"
    """
    all_filled = {}
    for value in values:
        value = value.strip()
        if value:
            squished = re.sub(r'\W+', '', value).lower()
            same_values = all_filled.get(squished, [])
            same_values.append(value)
            all_filled[squished] = same_values

    only_filled = []
    for _, vals in all_filled.items():
        longest = sorted(vals, key=len, reverse=True)[0]
        only_filled.append(ExactScore(longest, len(vals)))

    return sorted(only_filled, key=lambda s: s.count, reverse=True)


def top_partial_ratio(group,trustedUserWeights): #expecting group
    """Return the best partial ratio match from fuzzywuzzy module."""
    def convLine(line):
        line = '\n'.join([' '.join(ln.split()) for ln in str(line).splitlines()])
        return line
    values = group.apply(convLine)
    # generate user lookup dict
    userAttribution = values.reset_index(level=0, drop=True, inplace = False).to_dict()
    # invert it to {text that was ntered:user who entered it}
    userAttribution = {i[1]:i[0] for i in userAttribution.items()}
    scores = []
    
    for combo in combinations(values, 2):
        score = fuzz.partial_ratio(combo[0], combo[1])
        value = combo[0] if len(combo[0]) >= len(combo[1]) else combo[1]
        userName = userAttribution.get(value) # lookup the user who wrote the value
        scoreWeight = trustedUserWeights.get(userName, 0) # lookup that user's weight
        score = score + scoreWeight # add bonus points
        if score > 100: # enforce a ceiling
            score = 100
        
        scores.append(FuzzyRatioScore(score, value))
    scores = sorted(scores,
                    reverse=True,
                    key=lambda s: (s.score, len(s.value)))
    return scores[0]


def top_token_set_ratio(values):
    """Return the best token set ratio match from fuzzywuzzy module."""
    scores = []
    for combo in combinations(values, 2):
        score = fuzz.token_set_ratio(combo[0], combo[1])
        tokens_0 = len(combo[0].split())
        tokens_1 = len(combo[1].split())
        if tokens_0 > tokens_1:
            value = combo[0]
            tokens = tokens_0
        elif tokens_0 < tokens_1:
            value = combo[1]
            tokens = tokens_1
        else:
            tokens = tokens_0
            value = combo[1]
            if len(combo[0]) <= len(combo[1]):
                value = combo[0]
        scores.append(FuzzySetScore(score, value, tokens))

    ordered = sorted(
        scores,
        reverse=True,
        key=lambda s: (s.score, s.tokens, 1000000 - len(s.value)))
    return ordered[0]