from __future__ import unicode_literals
import torch
from torch import nn, optim
from torch.nn import init
import numpy as np

"""
author: Bowen Zhang
contact: bowen.zhang1@anu.edu.au
datetime: 8/28/2022 12:46 AM
"""

def log_sum_exp(tensor: torch.Tensor,
              dim: int = -1,
              keepdim: bool = False) -> torch.Tensor:
    """
    Compute logsumexp in a numerically stable way.
    This is mathematically equivalent to ``tensor.exp().sum(dim, keep=keepdim).log()``.
    This function is typically used for summing log probabilities.
    Parameters
    ----------
    tensor : torch.FloatTensor, required.
        A tensor of arbitrary size.
    dim : int, optional (default = -1)
        The dimension of the tensor to apply the logsumexp to.
    keepdim: bool, optional (default = False)
        Whether to retain a dimension of size one at the dimension we reduce over.
    """
    max_score, _ = tensor.max(dim, keepdim=keepdim)
    if keepdim:
        stable_vec = tensor - max_score
    else:
        stable_vec = tensor - max_score.unsqueeze(dim)
    return max_score + (stable_vec.exp().sum(dim, keepdim=keepdim)).log()

class CRFLayer(nn.Module):

  def __init__(self, tag_size, params):
    self.START_TAG = "[CLS]"
    self.END_TAG = "[SEP]"

    super(CRFLayer, self).__init__()
    # transition[i][j] means transition probability from j to i
    self.transition = nn.Parameter(torch.randn(tag_size, tag_size))
    self.tags = params.tags
    self.tag2idx = {tag: idx for idx, tag in enumerate(self.tags)}
    self.reset_parameters()

  def reset_parameters(self):
    init.normal_(self.transition)
    # initialize START_TAG, END_TAG probability in log space
    self.transition.detach()[self.tag2idx[self.START_TAG], :] = -10000
    self.transition.detach()[:, self.tag2idx[self.END_TAG]] = -10000

  def forward(self, feats, mask):
    """
    Arg:
      feats: (seq_len, batch_size, tag_size)
      mask: (seq_len, batch_size)
    Return:
      scores: (batch_size, )
    """
    seq_len, batch_size, tag_size = feats.size()
    # initialize alpha to zero in log space
    alpha = feats.new_full((batch_size, tag_size), fill_value=-10000)
    # alpha in START_TAG is 1
    alpha[:, self.tag2idx[self.START_TAG]] = 0
    for t, feat in enumerate(feats):
      # broadcast dimension: (batch_size, next_tag, current_tag)
      # emit_score is the same regardless of current_tag, so we broadcast along current_tag
      emit_score = feat.unsqueeze(-1) # (batch_size, tag_size, 1)
      # transition_score is the same regardless of each sample, so we broadcast along batch_size dimension
      transition_score = self.transition.unsqueeze(0) # (1, tag_size, tag_size)
      # alpha_score is the same regardless of next_tag, so we broadcast along next_tag dimension
      alpha_score = alpha.unsqueeze(1) # (batch_size, 1, tag_size)
      alpha_score = alpha_score + transition_score + emit_score
      # log_sum_exp along current_tag dimension to get next_tag alpha
      mask_t = mask[t].unsqueeze(-1)
      alpha = log_sum_exp(alpha_score, -1) * mask_t + alpha * (1 - mask_t) # (batch_size, tag_size)
    # arrive at END_TAG
    alpha = alpha + self.transition[self.tag2idx[self.END_TAG]].unsqueeze(0)

    return log_sum_exp(alpha, -1) # (batch_size, )

  def score_sentence(self, feats, tags, mask):
    """
    Arg:
      feats: (seq_len, batch_size, tag_size)
      tags: (seq_len, batch_size)
      mask: (seq_len, batch_size)
    Return:
      scores: (batch_size, )
    """
    seq_len, batch_size, tag_size = feats.size()
    scores = feats.new_zeros(batch_size)
    tags = torch.cat([tags.new_full((1, batch_size), fill_value=self.tag2idx[self.START_TAG]), tags], 0) # (seq_len + 1, batch_size)
    for t, feat in enumerate(feats):
      emit_score = torch.stack([f[next_tag] for f, next_tag in zip(feat, tags[t + 1])])
      transition_score = torch.stack([self.transition[tags[t + 1, b], tags[t, b]] for b in range(batch_size)])
      scores += (emit_score + transition_score) * mask[t]
    transition_to_end = torch.stack([self.transition[self.tag2idx[self.END_TAG], tag[mask[:, b].sum().long()]] for b, tag in enumerate(tags.transpose(0, 1))])
    scores += transition_to_end
    return scores

  def viterbi_decode(self, feats, mask):
    """
    :param feats: (seq_len, batch_size, tag_size)
    :param mask: (seq_len, batch_size)
    :return best_path: (seq_len, batch_size)
    """
    seq_len, batch_size, tag_size = feats.size()
    # initialize scores in log space
    scores = feats.new_full((batch_size, tag_size), fill_value=-10000)
    scores[:, self.tag2idx[self.START_TAG]] = 0
    pointers = []
    # forward
    for t, feat in enumerate(feats):
      # broadcast dimension: (batch_size, next_tag, current_tag)
      scores_t = scores.unsqueeze(1) + self.transition.unsqueeze(0)  # (batch_size, tag_size, tag_size)
      # max along current_tag to obtain: next_tag score, current_tag pointer
      scores_t, pointer = torch.max(scores_t, -1)  # (batch_size, tag_size), (batch_size, tag_size)
      scores_t += feat
      pointers.append(pointer)
      mask_t = mask[t].unsqueeze(-1)  # (batch_size, 1)
      scores = scores_t * mask_t + scores * (1 - mask_t)
    pointers = torch.stack(pointers, 0) # (seq_len, batch_size, tag_size)
    scores += self.transition[self.tag2idx[self.END_TAG]].unsqueeze(0)
    best_score, best_tag = torch.max(scores, -1)  # (batch_size, ), (batch_size, )
    # backtracking
    best_path = best_tag.unsqueeze(-1).tolist() # list shape (batch_size, 1)
    for i in range(batch_size):
      best_tag_i = best_tag[i]
      seq_len_i = int(mask[:, i].sum())
      for ptr_t in reversed(pointers[:seq_len_i, i]):
        # ptr_t shape (tag_size, )
        best_tag_i = ptr_t[best_tag_i].item()
        best_path[i].append(best_tag_i)
      # pop first tag
      best_path[i].pop()
      # reverse order
      best_path[i].reverse()
    return best_path
