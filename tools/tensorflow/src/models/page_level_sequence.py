
import tensorflow as tf

from src.builder import ModelBuilder

class PageLevelSequenceModelBuilder(ModelBuilder):

    def __init__(self,
                 page_size=3,
                 user_vocab_size=10,
                 item_vocab_size=10,
                 rnn_size=5,
                 item_events=['display', 'action'],
                 predicted_event='action',
                 eval_metrics='MAP@1',
                 loss_split_steps=500,
                 max_train_steps=500,
                 train_steps=500,
                 eval_steps=1,
                 filter_unrecognized=False):
        self._page_size = page_size
        self._user_vocab_size = user_vocab_size
        self._item_vocab_size = item_vocab_size
        self._rnn_size = rnn_size
        self._item_events = item_events
        self._predicted_event = predicted_event
        self._eval_metrics = eval_metrics
        self._loss_split_steps = loss_split_steps
        self._max_train_steps = max_train_steps
        self._train_steps = train_steps
        self._eval_steps = eval_steps
        self._filter_unrecognized = filter_unrecognized
        self._test_tensors = {}

    def test_tensors(self):
        return self._test_tensors

    def _event_item(self, name):
        event = tf.placeholder(
            tf.int32, shape=(None, None), name='%s_idx' % name)
        max_sequence_len = tf.shape(event)[1] / self._page_size
        if self._filter_unrecognized:
            recognized_item = event * tf.cast(event < self._item_vocab_size, tf.int32)
            return tf.reshape(
                recognized_item, [tf.shape(recognized_item)[0], max_sequence_len, self._page_size])
        else:
            return tf.reshape(event, [tf.shape(event)[0], max_sequence_len, self._page_size])

    def _step_wise_relu(self, input):
        relu_layer = tf.keras.layers.Dense(self._rnn_size, activation='relu')
        return relu_layer(input)

    def _get_rnn_output(self, input):
        rnn_layer = tf.keras.layers.GRU(self._rnn_size, return_sequences=True)
        return rnn_layer(input)

    def _compute_map_metrics(self, labels, logits, metric):
        K = metric.split('@')[1].split(',')
        updates = []
        for k in K:
            with tf.variable_scope('MAP_K%s' % k):
                map_value, map_update = tf.metrics.sparse_average_precision_at_k(
                    tf.cast(labels, tf.int64), logits, int(k))
                updates.append(map_update)
                tf.summary.scalar('MAP_K%s' % k, map_value)
        return updates

    def _compute_event_loss(self, rnn_output, indices, labels, softmax, metrics=None):
        batch_idx = tf.reshape(tf.slice(indices,
            begin=[0, 0],
            size=[tf.shape(indices)[0], 1]), [tf.shape(indices)[0], 1])
        step_idx = tf.reshape(tf.slice(indices,
            begin=[0, 1],
            size=[tf.shape(indices)[0], 1]) - 1, [tf.shape(indices)[0], 1])
        rnn_indices = tf.concat([batch_idx, step_idx], 1)
        used_output = tf.gather_nd(rnn_output, rnn_indices)
        valid_labels = tf.gather_nd(labels, indices)
        metric_update = []
        if self._loss_split_steps < self._max_train_steps and metrics == None:
            mask_idx = tf.reshape(tf.slice(indices,
                begin=[0, 1],
                size=[tf.shape(indices)[0], 1]) - 1, [-1])
            loss = 0
            with tf.variable_scope('mask'):
                for i in range(0, self._max_train_steps, self._loss_split_steps):
                    loss_mask = tf.logical_and(mask_idx >= i, mask_idx < i + self._loss_split_steps)
                    masked_labels = tf.boolean_mask(valid_labels, loss_mask)
                    masked_output = tf.boolean_mask(used_output, loss_mask)
                    masked_logits = softmax(masked_output)
                    masked_losses = tf.nn.sparse_softmax_cross_entropy_with_logits(
                        labels=masked_labels, logits=masked_logits)
                    loss += tf.reduce_sum(masked_losses)
        else:
            logits = softmax(used_output)
            losses = tf.nn.sparse_softmax_cross_entropy_with_logits(
                labels=valid_labels, logits=logits)
            loss = tf.reduce_sum(losses)
            if metrics != None:
                for metric in metrics.split(' '):
                    if 'MAP' in metric:
                        metric_update += self._compute_map_metrics(valid_labels, logits, metric)
        return loss, metric_update

    def _get_softmax_loss(self, sequence_length, rnn_output, label_by_event, softmax_by_event):
        length_limit = tf.minimum(
                tf.reshape(sequence_length, [-1]),
                self._max_train_steps)
        tf.summary.scalar('max_sequence_length', tf.reduce_max(length_limit))
        split_limit = tf.maximum(length_limit - self._eval_steps, 2)
        start_limit = tf.maximum(split_limit - self._train_steps - 1, 0)
        train_loss = 0
        eval_loss = 0
        num_train_labels = 0
        num_eval_labels = 0
        updates = []
        for key in self._item_events:
            non_zeros_indices = tf.cast(tf.where(label_by_event[key] > 0), tf.int32)
            batch_idx = tf.reshape(tf.slice(
                non_zeros_indices,
                begin=[0, 0],
                size=[tf.shape(non_zeros_indices)[0], 1]), [-1])
            step_idx = tf.reshape(tf.slice(
                non_zeros_indices,
                begin=[0, 1],
                size=[tf.shape(non_zeros_indices)[0], 1]), [-1])
            step_split_limit = tf.gather(split_limit, batch_idx)
            step_length_limit = tf.gather(length_limit, batch_idx)
            step_start_limit = tf.gather(start_limit, batch_idx)
            train_indices = tf.boolean_mask(
                non_zeros_indices, tf.logical_and(
                    step_idx > step_start_limit, step_idx < step_split_limit))
            eval_indices = tf.boolean_mask(
                non_zeros_indices, tf.logical_and(
                    step_idx >= step_split_limit, step_idx < step_length_limit))
            num_event_train_labels = tf.shape(train_indices)[0]
            num_event_eval_labels = tf.shape(eval_indices)[0]
            num_train_labels += num_event_train_labels
            num_eval_labels += num_event_eval_labels
            with tf.variable_scope(key):
                tf.summary.scalar('num_train_labels', num_event_train_labels)
                tf.summary.scalar('num_eval_labels', num_event_eval_labels)
                train_event_loss, _ = self._compute_event_loss(
                    rnn_output, train_indices, label_by_event[key], softmax_by_event[key])
                eval_event_loss, metric_update = self._compute_event_loss(
                    rnn_output, eval_indices, label_by_event[key],
                    softmax_by_event[key], metrics=self._eval_metrics)
            train_loss += train_event_loss
            eval_loss += eval_event_loss
            updates += metric_update
        train_loss = train_loss / tf.cast(tf.maximum(1, num_train_labels), tf.float64)
        eval_loss = eval_loss / tf.cast(tf.maximum(1, num_eval_labels), tf.float64)
        tf.summary.scalar('train_loss', train_loss)
        tf.summary.scalar('eval_loss', eval_loss)
        return train_loss, updates

    def _get_softmax_prediction(self, sequence_length, rnn_output, softmax_by_event):
        seq_idx = sequence_length - 1
        batch_idx = tf.expand_dims(tf.range(tf.shape(sequence_length)[0]), 1)
        output_idx = tf.concat([batch_idx, seq_idx], 1)
        sequence_output = tf.gather_nd(rnn_output, output_idx)
        probs_by_event = {}
        for key in self._item_events:
            logits = softmax_by_event[key](sequence_output)
            probs_by_event[key] = tf.nn.softmax(logits, name='%s_prob' % key)
        return probs_by_event[self._predicted_event]

    def _get_input(self, item_idx):
        user_idx = tf.placeholder(tf.int32, shape=(None, 1), name='user_idx')
        if self._filter_unrecognized:
            user_idx = user_idx * tf.cast(user_idx < self._user_vocab_size, tf.int32)
        #create item input embeddings
        event_embedder = {}
        for key in self._item_events:
            event_embedder[key] = tf.keras.layers.Embedding(self._item_vocab_size, self._rnn_size)
        #get embeddings for each event
        event_embedding = []
        for key in self._item_events:
            event_embedding.append(event_embedder[key](item_idx[key]))
        concated_event_embedding = tf.concat(event_embedding, 3)
        item_input = tf.reshape(concated_event_embedding,
                                [tf.shape(concated_event_embedding)[0],
                                 tf.shape(concated_event_embedding)[1],
                                 len(self._item_events) * self._page_size * self._rnn_size])
        #create user input embedding
        user_embedder = tf.keras.layers.Embedding(self._user_vocab_size, self._rnn_size)
        expanded_user = tf.tile(user_idx, [1, tf.shape(item_input)[1]])
        expanded_user_embedding = user_embedder(expanded_user)
        all_input = tf.concat([item_input, expanded_user_embedding], 2)
        return all_input


    def build_model(self):
        #get sequence length as input for each sequence in the batch
        sequence_length = tf.placeholder(tf.int32, shape=(None, 1), name='sequence_length_val')

        #get other inputs
        item_idx = {}
        for key in self._item_events:
            item_idx[key] = self._event_item(key)
        with tf.variable_scope('embedding'):
            all_input = self._get_input(item_idx)

        #go through relu to have [batch, sequence_length, relu_output]
        with tf.variable_scope('relu'):
            relu_output = self._step_wise_relu(all_input)

        #go through rnn and get all the states output [batch, sequence_length, rnn_state]
        with tf.variable_scope('rnn'):
            rnn_output = self._get_rnn_output(relu_output)

        #create item softmax weights and biases
        with tf.variable_scope('softmax'):
            softmax_layer = {}
            for key in self._item_events:
                softmax_layer[key] = tf.keras.layers.Dense(self._item_vocab_size)

            #get softmax losses for all steps excluding the zeros
            with tf.variable_scope('loss'):
                loss, updates = self._get_softmax_loss(
                    sequence_length, rnn_output, item_idx, softmax_layer)

            #get softmax prediction by taking into account all steps in the sequences excluding the zeros
            with tf.variable_scope('prediction'):
                self._get_softmax_prediction(
                    sequence_length, rnn_output, softmax_layer)

        return loss, updates

    def build_optimizer(self, loss, learning_rate):
        return tf.train.RMSPropOptimizer(learning_rate).minimize(loss, name='upate_op')