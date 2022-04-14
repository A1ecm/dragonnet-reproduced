# from experiment.models import *
from experiment.models2 import *
import os
import glob
import argparse
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm
from sklearn.model_selection import train_test_split
from experiment.data import load_treatment_and_outcome, load_and_format_covariates
import numpy as np


def _split_output(yt_hat, t, y, y_scaler, x, index):
    yt_hat = yt_hat.detach().cpu().numpy()
    q_t0 = y_scaler.inverse_transform(yt_hat[:, 0].reshape(-1, 1).copy())
    q_t1 = y_scaler.inverse_transform(yt_hat[:, 1].reshape(-1, 1).copy())
    g = yt_hat[:, 2].copy()

    if yt_hat.shape[1] == 4:
        eps = yt_hat[:, 3][0]
    else:
        eps = np.zeros_like(yt_hat[:, 2])

    y = y_scaler.inverse_transform(y.copy())
    var = "average propensity for treated: {} and untreated: {}".format(g[t.squeeze() == 1.].mean(),
                                                                        g[t.squeeze() == 0.].mean())
    print(var)

    return {'q_t0': q_t0, 'q_t1': q_t1, 'g': g, 't': t, 'y': y, 'x': x, 'index': index, 'eps': eps}


def train(train_loader, net, optimizer, criterion):
    """
    Trains network for one epoch in batches.

    Args:
        train_loader: Data loader for training set.
        net: Neural network model.
        optimizer: Optimizer (e.g. SGD).
        criterion: Loss function (e.g. cross-entropy loss).
    """

    avg_loss = 0
    correct = 0
    total = 0

    # iterate through batches
    for i, data in enumerate(train_loader):
        # get the inputs; data is a list of [inputs, labels]
        inputs, labels = data

        # torch.autograd.set_detect_anomaly(True)

        # zero the parameter gradients
        optimizer.zero_grad()

        # forward + backward + optimize
        outputs = net(inputs)
        # print(outputs)
        loss = criterion(outputs, labels)
        # print(loss)
        # reg = 0
        # reg_lambda = 1
        # for param in net.parameters():
        #     reg += 0.5 * (param ** 2).sum()  # you can replace it with abs().sum() to get L1 regularization
        # loss = criterion(outputs, labels) + reg_lambda * reg  # make the regularization part of the loss
        # print(loss)
        loss.backward()
        # for name, param in net.named_parameters():
        #     print(name, torch.isnan(param.grad).any())
        # print(param.grad)
        # nn.utils.clip_grad_norm_(net.parameters(), clip_value=5.0)
        optimizer.step()

        # keep track of loss and accuracy
        avg_loss += loss
        # _, predicted = torch.max(outputs.data, 1)
        # total += labels.size(0)
        # correct += (predicted == labels).sum().item()

    return avg_loss / len(train_loader)  # , 100 * correct / total


def normalize(x):
    x_normed = x / x.max(0, keepdim=True)[0]
    return x_normed


def train_and_predict_dragons(t, y_unscaled, x, targeted_regularization=True, output_dir='',
                              knob_loss=dragonnet_loss_binarycross, ratio=1., dragon='', val_split=0.2, batch_size=64):
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    print(device)

    y_scaler = StandardScaler()
    y = y_scaler.fit_transform(y_unscaled)
    train_outputs = []
    test_outputs = []
    runs = 5
    for i in range(runs):

        if dragon == 'tarnet':
            print('I am here making tarnet')
            net = TarNet(x.shape[1]).cuda()

        elif dragon == 'dragonnet':
            print("I am here making dragonnet")
            net = DragonNet(x.shape[1]).cuda()

        # metrics = [regression_loss, binary_classification_loss, treatment_accuracy, track_epsilon]
        #
        if targeted_regularization:
            loss = make_tarreg_loss(ratio=ratio, dragonnet_loss=knob_loss)
        else:
            loss = knob_loss

        # loss = knob_loss
        # for reporducing the IHDP experimemt

        i = 0
        torch.manual_seed(i)
        np.random.seed(i)
        # train_index, test_index = train_test_split(np.arange(x.shape[0]), test_size=0., random_state=1)
        train_index = np.arange(x.shape[0])
        test_index = train_index

        x_train, x_test = x[train_index], x[test_index]
        y_train, y_test = y[train_index], y[test_index]
        t_train, t_test = t[train_index], t[test_index]

        yt_train = np.concatenate([y_train, t_train], 1)

        tensors_train = torch.from_numpy(x_train).float().cuda(), torch.from_numpy(yt_train).float().cuda()
        train_loader = DataLoader(TensorDataset(*tensors_train), batch_size=100)

        import time;
        start_time = time.time()

        epochs1 = 100
        epochs2 = 300

        optimizer_Adam = optim.Adam([{'params': net.representation_block.parameters()},
                                     {'params': net.t_predictions.parameters()},
                                     {'params': net.t0_head.parameters(), 'weight_decay': 0.01},
                                     {'params': net.t1_head.parameters(), 'weight_decay': 0.01}], lr=1e-3)
        optimizer_SGD = optim.SGD([{'params': net.representation_block.parameters()},
                                   {'params': net.t_predictions.parameters()},
                                   {'params': net.t0_head.parameters(), 'weight_decay': 0.01},
                                   {'params': net.t1_head.parameters(), 'weight_decay': 0.01}], lr=1e-5, momentum=0.9)

        scheduler_Adam = optim.lr_scheduler.ReduceLROnPlateau(optimizer=optimizer_Adam, mode='min', factor=0.5,
                                                              patience=5,
                                                              threshold=1e-8, cooldown=0, min_lr=0)
        scheduler_SGD = optim.lr_scheduler.ReduceLROnPlateau(optimizer=optimizer_SGD, mode='min', factor=0.5,
                                                             patience=5,
                                                             threshold=0, cooldown=0, min_lr=0)
        train_loss = 0
        for epoch in range(epochs1):
            # Train on data
            train_loss = train(train_loader, net, optimizer_Adam, loss)
            # print(f"Epoch: {epoch + 1}, loss: {train_loss}")

            scheduler_Adam.step(train_loss)
        print(f"loss: {train_loss}")
        # # Test on data
        # test_loss, test_acc = test(test_loader, dragonnet, loss)
        for epoch in range(epochs2):
            # Train on data
            train_loss = train(train_loader, net, optimizer_SGD, loss)
            # print(f"Epoch: {epoch + 1}, loss: {train_loss}")

            scheduler_SGD.step(train_loss)
        print(f"loss: {train_loss}")

        elapsed_time = time.time() - start_time
        print("***************************** elapsed_time is: ", elapsed_time)

        # yt_hat_test = dragonnet.predict(x_test)
        # yt_hat_train = dragonnet.predict(x_train)

        yt_hat_test = net(torch.from_numpy(x_test).float().cuda())
        yt_hat_train = net(torch.from_numpy(x_train).float().cuda())

        test_outputs += [_split_output(yt_hat_test, t_test, y_test, y_scaler, x_test, test_index)]
        train_outputs += [_split_output(yt_hat_train, t_train, y_train, y_scaler, x_train, train_index)]

    return test_outputs, train_outputs


def run_acic(data_base_dir='../../data/', output_dir='../../dragonnet/',
             knob_loss=dragonnet_loss_binarycross,
             ratio=1., dragon='', folder='carefully_selected'):
    print("************************************** the output directory is: ", output_dir)
    covariate_csv = os.path.join(data_base_dir, 'x.csv')
    x_raw = load_and_format_covariates(covariate_csv)
    simulation_dir = os.path.join(data_base_dir, folder)

    simulation_files = sorted(glob.glob("{}/*".format(simulation_dir)))

    for idx, simulation_file in enumerate(simulation_files):
        cf_suffix = "_cf"
        file_extension = ".csv"
        if simulation_file.endswith(cf_suffix + file_extension):
            continue
        ufid = os.path.basename(simulation_file)[:-4]

        t, y, sample_id, x = load_treatment_and_outcome(x_raw, simulation_file)
        ufid_output_dir = os.path.join(output_dir, str(ufid))

        os.makedirs(ufid_output_dir, exist_ok=True)
        np.savez_compressed(os.path.join(ufid_output_dir, "simulation_outputs.npz"),
                            t=t, y=y, sample_id=sample_id, x=x)

        for is_targeted_regularization in [True, False]:
            print("Is targeted regularization: {}".format(is_targeted_regularization))
            test_outputs, train_outputs = train_and_predict_dragons(t, y, x,
                                                                    targeted_regularization=is_targeted_regularization,
                                                                    output_dir=ufid_output_dir,
                                                                    knob_loss=knob_loss, ratio=ratio, dragon=dragon,
                                                                    val_split=0.2, batch_size=512)
            if is_targeted_regularization:
                train_output_dir = os.path.join(ufid_output_dir, "targeted_regularization")
            else:
                train_output_dir = os.path.join(ufid_output_dir, "baseline")

            os.makedirs(train_output_dir, exist_ok=True)
            for num, output in enumerate(test_outputs):
                np.savez_compressed(os.path.join(train_output_dir, "{}_replication_test.npz".format(num)),
                                    **output)

            for num, output in enumerate(train_outputs):
                np.savez_compressed(os.path.join(train_output_dir, "{}_replication_train.npz".format(num)),
                                    **output)


def turn_knob(data_base_dir='/Users/claudiashi/data/test/', knob='dragonnet', folder='a',
              output_base_dir=' /Users/claudiashi/result/experiment/'):
    output_dir = os.path.join(output_base_dir, knob)

    if knob == 'dragonnet':
        run_acic(data_base_dir=data_base_dir, output_dir=output_dir, folder=folder,
                 knob_loss=dragonnet_loss_binarycross, dragon='dragonnet')

    if knob == 'tarnet':
        run_acic(data_base_dir=data_base_dir, output_dir=output_dir, folder=folder,
                 knob_loss=dragonnet_loss_binarycross, dragon='tarnet')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data_base_dir', type=str, help="path to directory LBIDD")
    parser.add_argument('--knob', type=str, default='dragonnet',
                        help="dragonnet or tarnet")

    parser.add_argument('--folder', type=str, help='which datasub directory')
    parser.add_argument('--output_base_dir', type=str, help="directory to save the output")

    args = parser.parse_args()

    turn_knob(args.data_base_dir, args.knob, args.folder, args.output_base_dir)


if __name__ == '__main__':
    main()