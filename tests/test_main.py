#test main
import os
import glob
import pytest
import pandas as pd
import numpy as np
from skimage import io

from deepforest import main
from deepforest import get_data
from deepforest import model

from pytorch_lightning import Trainer
from pytorch_lightning.callbacks import Callback


@pytest.fixture()
def m():
    m = main.deepforest()
    m.config["train"]["csv_file"] = get_data("example.csv") 
    m.config["train"]["root_dir"] = os.path.dirname(get_data("example.csv"))
    m.config["train"]["fast_dev_run"] = True
    m.config["batch_size"] = 2
       
    m.config["validation"]["csv_file"] = get_data("example.csv") 
    m.config["validation"]["root_dir"] = os.path.dirname(get_data("example.csv"))
    
    #Just use the backbone trained model for the moment, or else no predictions because of random weights
    m.model = model.load_backbone()
    
    m.create_trainer()
    
    return m

def test_main():
    from deepforest import main

def test_train(m):
    m.run_train()
    
def test_predict_image_empty(m):
    image = np.random.random((400,400,3))
    prediction = m.predict_image(image = image)
    
    assert prediction is None
    
def test_predict_image_fromfile(m):
    path = get_data(path="2019_YELL_2_528000_4978000_image_crop2.png")
    prediction = m.predict_image(path = path)
    
    assert isinstance(prediction, pd.DataFrame)
    assert set(prediction.columns) == {"xmin","ymin","xmax","ymax","label","scores"}

def test_predict_image_fromarray(m):
    image = get_data(path="2019_YELL_2_528000_4978000_image_crop2.png")
    image = io.imread(image)
    prediction = m.predict_image(image = image)
    
    assert isinstance(prediction, pd.DataFrame)
    assert set(prediction.columns) == {"xmin","ymin","xmax","ymax","label","scores"}

def test_predict_return_plot(m):
    image = get_data(path="2019_YELL_2_528000_4978000_image_crop2.png")
    image = io.imread(image)
    plot = m.predict_image(image = image, return_plot=True)
    assert not isinstance(plot, pd.DataFrame)
            
def test_predict_file(m, tmpdir):
    csv_file = get_data("example.csv")
    df = m.predict_file(csv_file, root_dir = os.path.dirname(csv_file), savedir=tmpdir)
    assert set(df.columns) == {"xmin","ymin","xmax","ymax","label","scores","image_path"}
    
    printed_plots = glob.glob("{}/*.png".format(tmpdir))
    assert len(printed_plots) == 1

def test_predict_tile(m):
    #test raster prediction 
    raster_path = get_data(path= 'OSBS_029.tif')
    prediction = m.predict_tile(raster_path = raster_path,
                                            patch_size = 300,
                                            patch_overlap = 0.5,
                                            return_plot = False)
    assert isinstance(prediction, pd.DataFrame)
    assert set(prediction.columns) == {"xmin","ymin","xmax","ymax","label","score"}
    assert not prediction.empty

    #test soft-nms method
    soft_nms_pred = m.predict_tile(raster_path = raster_path,
                                            patch_size = 300,
                                            patch_overlap = 0.5,
                                            return_plot = False,
                                            use_soft_nms =True)
    assert isinstance(soft_nms_pred, pd.DataFrame)
    assert set(soft_nms_pred.columns) == {"xmin","ymin","xmax","ymax","label","score"}
    assert not soft_nms_pred.empty

    #test predict numpy image
    image = io.imread(raster_path)
    prediction = m.predict_tile(image = image,
                                patch_size = 300,
                                patch_overlap = 0.5,
                                return_plot = False)
    assert not prediction.empty

    # Test no non-max suppression
    prediction = m.predict_tile(raster_path = raster_path,
                                       patch_size=300,
                                       patch_overlap=0,
                                       return_plot=False)
    assert not prediction.empty
    
def test_evaluate(m):
    csv_file = get_data("example.csv")
    root_dir = os.path.dirname(csv_file)
    precision, recall = m.evaluate(csv_file, root_dir, iou_threshold = 0.5)
    
def test_train_callbacks(m):
    csv_file = get_data("example.csv") 
    root_dir = os.path.dirname(csv_file)
    train_ds = m.load_dataset(csv_file, root_dir=root_dir)
    
    class MyPrintingCallback(Callback):
    
        def on_init_start(self, trainer):
            print('Starting to init trainer!')
    
        def on_init_end(self, trainer):
            print('trainer is init now')
    
        def on_train_end(self, trainer, pl_module):
            print('do something when training ends')
    
    trainer = Trainer(callbacks=[MyPrintingCallback()])
    
    trainer = Trainer(fast_dev_run=True)
    trainer.fit(m, train_ds)