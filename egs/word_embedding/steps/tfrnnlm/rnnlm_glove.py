# -*- coding:utf-8 -*-

import os
import sys
import time
import math
import reader
import random
import inspect
import collections
import numpy as np
import tensorflow as tf
from scipy.sparse import lil_matrix

reload(sys) 
sys.setdefaultencoding("utf-8")

os.environ["CUDA_VISIBLE_DEVICES"] = "5"
config = tf.ConfigProto()
config.gpu_options.per_process_gpu_memory_fraction = 0.95
session = tf.Session(config = config)

flags = tf.flags
logging = tf.logging
flags.DEFINE_string("data-path", None, "Where the training/test data is stored.")
flags.DEFINE_string("vocab-path", None, "Where the wordlist file is stored.")
flags.DEFINE_string("save-path", None, "Model output directory.")
flags.DEFINE_integer("hidden-size", 200, "hidden dim of RNN")
flags.DEFINE_integer("num-layers", 2, "number of layers of RNN")
flags.DEFINE_integer("batch-size", 64, "batch size of RNN training")
flags.DEFINE_float("keep-prob", 1.0, "Keep Probability of Dropout")
flags.DEFINE_integer("max-epoch", 30, "The number of max epoch")
FLAGS = flags.FLAGS

class Config(object):
	"""Small config."""
	init_scale = 0.1
	learning_rate = 1.0
	max_grad_norm = 1
	num_layers = 1
	num_steps = 20
	hidden_size = 200
	max_epoch = 4
	max_max_epoch = 30
	keep_prob = 1
	lr_decay = 0.8
	batch_size = 64

class RnnlmInput(object):
	"""The input data."""
	def __init__(self, config, data, name = None):
		self.batch_size = batch_size = config.batch_size
		self.num_steps = num_steps = config.num_steps
		self.epoch_size = ((len(data) // batch_size) - 1) // num_steps
		self.input_data, self.targets = reader.rnnlm_producer(
			data, batch_size, num_steps, name = name)

class RnnlmModel(object):
	"""The RNNLM model."""
	def __init__(self, is_training, config, input_, glove_embeddings):
		self._input = input_
		batch_size = input_.batch_size
		num_steps = input_.num_steps
		hidden_size = config.hidden_size
		vocab_size = config.vocab_size

		def rnn_cell():
			if 'reuse' in inspect.getargspec(
					tf.contrib.rnn.BasicRNNCell.__init__).args:
				return tf.contrib.rnn.BasicRNNCell(hidden_size, reuse=tf.get_variable_scope().reuse)
			else:
				return tf.contrib.rnn.BasicRNNCell(hidden_size)
		attn_cell = rnn_cell

		if is_training and config.keep_prob < 1:
			def attn_cell():
				return tf.contrib.rnn.DropoutWrapper(
						rnn_cell(), output_keep_prob=config.keep_prob)

		self.cell = tf.contrib.rnn.MultiRNNCell(
				[attn_cell() for _ in range(config.num_layers)], state_is_tuple=True)

		self._initial_state = self.cell.zero_state(batch_size, tf.float32)
		self._initial_state_single = self.cell.zero_state(1, tf.float32)

		self.initial = tf.reshape(tf.stack(axis=0, values=self._initial_state_single), 
			[config.num_layers, 1, hidden_size], name="test_initial_state")

		# first implement the less efficient version
		test_word_in = tf.placeholder(tf.int32, [1, 1], name="test_word_in")

		state_placeholder = tf.placeholder(tf.float32, [config.num_layers, 1, hidden_size], name="test_state_in")
		# unpacking the input state context 
		l = tf.unstack(state_placeholder, axis=0)
		test_input_state = tuple([l[idx] for idx in range(config.num_layers)])

		# self.embedding = tf.get_variable("embedding", [vocab_size, hidden_size], dtype = tf.float32)
		# inputs = tf.nn.embedding_lookup(self.embedding, input_.input_data)
		# test_inputs = tf.nn.embedding_lookup(self.embedding, test_word_in)

		with tf.device("/cpu:0"):
			embed_init = tf.constant_initializer(glove_embeddings, dtype = tf.float32)
			self.embedding = tf.get_variable("embedding", shape = [vocab_size, hidden_size], 
				dtype = tf.float32, initializer = embed_init)
			
			inputs = tf.nn.embedding_lookup(self.embedding, input_.input_data)
			test_inputs = tf.nn.embedding_lookup(self.embedding, test_word_in)

		# test time
		with tf.variable_scope("RNN"):
			(test_cell_output, test_output_state) = self.cell(test_inputs[:, 0, :], test_input_state)

		test_state_out = tf.reshape(tf.stack(axis=0, values=test_output_state), 
			[config.num_layers, 1, hidden_size], name="test_state_out")
		test_cell_out = tf.reshape(test_cell_output, [1, hidden_size], name="test_cell_out")
		# above is the first part of the graph for test
		# test-word-in
		#               > ---- > test-state-out
		# test-state-in        > test-cell-out

		# below is the 2nd part of the graph for test
		# test-word-out
		#               > prob(word | test-word-out)
		# test-cell-in
		test_word_out = tf.placeholder(tf.int32, [1, 1], name="test_word_out")
		cellout_placeholder = tf.placeholder(tf.float32, [1, hidden_size], name="test_cell_in")

		softmax_w = tf.get_variable("softmax_w", [hidden_size, vocab_size], dtype=tf.float32)
		softmax_b = tf.get_variable("softmax_b", [vocab_size], dtype=tf.float32)

		test_logits = tf.matmul(cellout_placeholder, softmax_w) + softmax_b
		test_softmaxed = tf.nn.log_softmax(test_logits)

		p_word = test_softmaxed[0, test_word_out[0,0]]
		test_out = tf.identity(p_word, name="test_out")

		if is_training and config.keep_prob < 1:
			inputs = tf.nn.dropout(inputs, config.keep_prob)

		# Simplified version of models/tutorials/rnn/rnn.py's rnn().
		# This builds an unrolled LSTM for tutorial purposes only.
		# In general, use the rnn() or state_saving_rnn() from rnn.py.
		#
		# The alternative version of the code below is:
		#
		# inputs = tf.unstack(inputs, num=num_steps, axis=1)
		# outputs, state = tf.contrib.rnn.static_rnn(
		#     cell, inputs, initial_state=self._initial_state)
		outputs = []
		state = self._initial_state
		with tf.variable_scope("RNN"):
			for time_step in range(num_steps):
				if time_step > -1: tf.get_variable_scope().reuse_variables()
				(cell_output, state) = self.cell(inputs[:, time_step, :], state)
				outputs.append(cell_output)

		output = tf.reshape(tf.stack(axis=1, values=outputs), [-1, hidden_size])
		logits = tf.matmul(output, softmax_w) + softmax_b
		loss = tf.contrib.legacy_seq2seq.sequence_loss_by_example(
				[logits],
				[tf.reshape(input_.targets, [-1])],
				[tf.ones([batch_size * num_steps], dtype=tf.float32)])
		self._cost = cost = tf.reduce_sum(loss) / batch_size
		self._final_state = state

		if not is_training:
			return

		self._lr = tf.Variable(0.0, trainable=False)
		tvars = tf.trainable_variables()
		grads, _ = tf.clip_by_global_norm(tf.gradients(cost, tvars), config.max_grad_norm)
		optimizer = tf.train.MomentumOptimizer(self._lr, 0.9)
		self._train_op = optimizer.apply_gradients(
				zip(grads, tvars),
				global_step=tf.contrib.framework.get_or_create_global_step())
		self._new_lr = tf.placeholder(
				tf.float32, shape=[], name="new_learning_rate")
		self._lr_update = tf.assign(self._lr, self._new_lr)

	def assign_lr(self, session, lr_value):
		session.run(self._lr_update, feed_dict = {self._new_lr: lr_value})

	@property
	def input(self):
		return self._input

	@property
	def initial_state(self):
		return self._initial_state

	@property
	def cost(self):
		return self._cost

	@property
	def final_state(self):
		return self._final_state

	@property
	def lr(self):
		return self._lr

	@property
	def train_op(self):
		return self._train_op

def run_epoch(session, model, eval_op=None, verbose=False):
	"""Runs the model on the given data."""
	start_time = time.time()
	costs = 0.0
	iters = 0
	state = session.run(model.initial_state)

	fetches = {
		"cost": model.cost,
		"final_state": model.final_state,
	}
	if eval_op is not None:
		fetches["eval_op"] = eval_op

	for step in range(model.input.epoch_size):
		feed_dict = {}
		for i, h in enumerate(model.initial_state):
			feed_dict[h] = state[i]

		vals = session.run(fetches, feed_dict)
		cost = vals["cost"]
		state = vals["final_state"]
		costs += cost
		iters += model.input.num_steps
		if verbose and step % (model.input.epoch_size // 10) == 10:
			print("%.3f perplexity: %.3f speed: %.0f wps" %
				 (step * 1.0 / model.input.epoch_size, np.exp(costs / iters),
				 iters * model.input.batch_size / (time.time() - start_time)))
	return np.exp(costs / iters)

data_index = 0
def generate_batch(train_data, embed_batch_size, num_skips, skip_window):
	global data_index
	assert embed_batch_size % num_skips == 0
	assert num_skips <= 2 * skip_window
	batch = np.ndarray(shape=(embed_batch_size), dtype=np.int32)
	labels = np.ndarray(shape=(embed_batch_size, 1), dtype=np.int32)
	weights = np.ndarray(shape=(embed_batch_size), dtype=np.float32)
	span = 2 * skip_window + 1
	buffer = collections.deque(maxlen=span)
	for _ in range(span):
		buffer.append(train_data[data_index])
		data_index = (data_index + 1) % len(train_data)
	for i in range(embed_batch_size // num_skips):
		target = skip_window
		targets_to_avoid = [ skip_window ]
		for j in range(num_skips):
			while target in targets_to_avoid:
				target = random.randint(0, span - 1)
			targets_to_avoid.append(target)
			batch[i * num_skips + j] = buffer[skip_window]
			labels[i * num_skips + j, 0] = buffer[target]
			weights[i * num_skips + j] = abs(1.0/(target - skip_window))
		buffer.append(train_data[data_index])
		data_index = (data_index + 1) % len(train_data)
	return batch, labels, weights

# 读取数据
raw_data = reader.rnnlm_raw_data(FLAGS.data_path, FLAGS.vocab_path)
train_data, valid_data, _, word_map = raw_data
# train_data: data
reverse_wordmap = dict(zip(word_map.values(), word_map.keys()))
vocabulary_size = len(word_map)
cooc_data_index = 0
cooc_mat = lil_matrix((vocabulary_size, vocabulary_size), dtype=np.float32)
dataset_size = len(train_data)
print(cooc_mat.shape)
def generate_cooc(train_data, embed_batch_size, num_skips, skip_window):
	data_index = 0
	print('Running %d iterations to compute the co-occurance matrix' %( dataset_size // embed_batch_size))
	for i in range(dataset_size//embed_batch_size):
		if i > 0 and i % 10000 == 0:
			print('\tFinished %d iterations' % i)
		batch, labels, weights = generate_batch(
			train_data = train_data,
			embed_batch_size = embed_batch_size,
			num_skips = num_skips,
			skip_window = skip_window) # increments data_index automatically
		labels = labels.reshape(-1)

		for inp,lbl,w in zip(batch,labels,weights):
			cooc_mat[inp,lbl] += (1.0*w)

def get_config():
	return Config()

def main(_):
	if not FLAGS.data_path:
		raise ValueError("Must set --data_path to RNNLM data directory")

	# word embedding参数设置
	embed_batch_size = 128
	embedding_size = 200
	skip_window = 4
	num_skips = 8

	valid_size = 16
	valid_window = 100
	embed_num_steps = 100001

	# Validation set consist of 50 infrequent words and 50 frequent words
	valid_examples = np.array(random.sample(range(valid_window), valid_size//2))
	valid_examples = np.append(
		valid_examples,
		random.sample(range(1000, 1000 + valid_window),valid_size // 2))
	epsilon = 1 # used for the stability of log in the loss function
	generate_cooc(train_data, 8, num_skips, skip_window)

	config = get_config()
	config.vocab_size = len(word_map)
	config.hidden_size = FLAGS.hidden_size
	config.num_layers = FLAGS.num_layers
	config.batch_size = FLAGS.batch_size
	config.keep_prob= FLAGS.keep_prob
	config.max_max_epoch = FLAGS.max_epoch


	eval_config = get_config()
	eval_config.batch_size = 1
	eval_config.num_steps = 1

	graph_glove= tf.Graph()
	with graph_glove.as_default():
		train_dataset = tf.placeholder(tf.int32, shape=[embed_batch_size], name='train_dataset')
		train_labels = tf.placeholder(tf.int32, shape=[embed_batch_size], name='train_labels')
		valid_dataset = tf.constant(valid_examples, dtype=tf.int32, name='valid_dataset')

		# Variables.
		embeddings = tf.Variable(
				tf.random_uniform([vocabulary_size, embedding_size], -1.0, 1.0), name = 'embeddings')
		bias_embeddings = tf.Variable(tf.random_uniform([vocabulary_size],0.0,0.01, dtype = tf.float32),
			name = 'embeddings_bias')

		# Model.
		# Look up embeddings for inputs.
		embed_in = tf.nn.embedding_lookup(embeddings, train_dataset)
		embed_out = tf.nn.embedding_lookup(embeddings, train_labels)
		embed_bias_in = tf.nn.embedding_lookup(bias_embeddings,train_dataset)
		embed_bias_out = tf.nn.embedding_lookup(bias_embeddings,train_labels)

		# weights used in the cost function
		weights_x = tf.placeholder(tf.float32,shape = [embed_batch_size],name = 'weights_x') 
		x_ij = tf.placeholder(tf.float32,shape = [embed_batch_size],name = 'x_ij')

		# Compute the loss defined in the paper. Note that I'm not following the exact equation given (which is computing a pair of words at a time)
		# I'm calculating the loss for a batch at one time, but the calculations are identical.
		# I also made an assumption about the bias, that it is a smaller type of embedding
		loss = tf.reduce_mean(
			weights_x * (tf.reduce_sum(embed_in*embed_out, axis=1) + embed_bias_in + embed_bias_out - tf.log(epsilon+x_ij))**2)

		# Optimizer.
		optimizer = tf.train.AdagradOptimizer(1.0).minimize(loss)
		# Compute the similarity between minibatch examples and all embeddings.
		norm = tf.sqrt(tf.reduce_sum(tf.square(embeddings), 1, keep_dims = True))
		normalized_embeddings = embeddings / norm
		valid_embeddings = tf.nn.embedding_lookup(normalized_embeddings, valid_dataset)
		similarity = tf.matmul(valid_embeddings, tf.transpose(normalized_embeddings))

	with tf.Session(graph = graph_glove) as session:
		tf.global_variables_initializer().run()
		print('Initialized')

		average_loss = 0
		for step in range(embed_num_steps):
			batch_data, batch_labels, batch_weights = generate_batch(
					train_data, embed_batch_size, num_skips, skip_window) # generate a single batch (data,labels,co-occurance weights)
			batch_weights = [] # weighting used in the loss function
			batch_xij = [] # weighted frequency of finding i near j
			for inp,lbl in zip(batch_data,batch_labels.reshape(-1)):        
					batch_weights.append((np.asscalar(cooc_mat[inp,lbl])/100.0)**0.75)
					batch_xij.append(cooc_mat[inp,lbl])
			batch_weights = np.clip(batch_weights,-100,1)
			batch_xij = np.asarray(batch_xij)

			feed_dict = {train_dataset : batch_data.reshape(-1), train_labels : batch_labels.reshape(-1),
									weights_x:batch_weights,x_ij:batch_xij}
			_, l = session.run([optimizer, loss], feed_dict=feed_dict)

			average_loss += l
			if step % 2000 == 0:
				if step > 0:
					average_loss = average_loss / 2000
				print('Average loss at step %d: %f' % (step, average_loss))
				average_loss = 0
			# note that this is expensive (~20% slowdown if computed every 500 steps)
			if step % 10000 == 0:
				sim = similarity.eval()
				for i in range(valid_size):
					valid_word = reverse_wordmap[valid_examples[i]]
					top_k = 8 # number of nearest neighbors
					nearest = (-sim[i, :]).argsort()[1:top_k+1]
					log = 'Nearest to %s:' % valid_word
					for k in range(top_k):
						close_word = reverse_wordmap[nearest[k]]
						log = '%s %s,' % (log, close_word)
					print(log)
		final_embeddings = normalized_embeddings.eval()

	graph_rnnlm = tf.Graph()
	with graph_rnnlm.as_default():
		initializer = tf.random_uniform_initializer(-config.init_scale, config.init_scale)
		with tf.name_scope("Train"):
			train_input = RnnlmInput(config = config, data = train_data, name = "TrainInput")
			with tf.variable_scope("Model", reuse = None, initializer = initializer):
				m = RnnlmModel(is_training = True, config = config, input_ = train_input,
					glove_embeddings = final_embeddings)
			tf.summary.scalar("Training Loss", m.cost)
			tf.summary.scalar("Learning Rate", m.lr)

		with tf.name_scope("Valid"):
			valid_input = RnnlmInput(config=config, data=valid_data, name="ValidInput")
			with tf.variable_scope("Model", reuse=True, initializer=initializer):
				mvalid = RnnlmModel(is_training=False, config=config, input_=valid_input,
					glove_embeddings = final_embeddings)
			tf.summary.scalar("Validation Loss", mvalid.cost)

		sv = tf.train.Supervisor(logdir=FLAGS.save_path)
		with sv.managed_session() as session:
			for i in range(config.max_max_epoch):
				lr_decay = config.lr_decay ** max(i + 1 - config.max_epoch, 0.0)

				m.assign_lr(session, config.learning_rate * lr_decay)

				print("Epoch: %d Learning rate: %.3f" % (i + 1, session.run(m.lr)))
				train_perplexity = run_epoch(session, m, eval_op=m.train_op, verbose=True)

				print("Epoch: %d Train Perplexity: %.3f" % (i + 1, train_perplexity))
				valid_perplexity = run_epoch(session, mvalid)
				print("Epoch: %d Valid Perplexity: %.3f" % (i + 1, valid_perplexity))

			if FLAGS.save_path:
				print("Saving model to %s." % FLAGS.save_path)
				sv.saver.save(session, FLAGS.save_path)

if __name__ == "__main__":
	tf.app.run()
